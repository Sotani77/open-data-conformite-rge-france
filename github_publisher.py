"""Synchronisation GitHub en un commit, mise à jour non forcée de la branche."""

import base64
import hashlib
from pathlib import Path
from urllib.parse import quote

from common import Api, ApiError, PipelineError


def git_blob_sha(content):
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()


class GitHubPublisher(Api):
    def __init__(self, settings, http):
        super().__init__(settings.github_api_url, http)
        self.settings = settings
        self.repo_path = f"repos/{settings.github_owner}/{settings.github_repo}"

    def preflight(self):
        return self.call("GET", "user")

    def ensure_repository(self):
        s = self.settings
        try:
            repo = self.call("GET", self.repo_path)
        except ApiError as exc:
            if exc.status != 404:
                raise
            user = self.preflight()
            if user["login"].lower() == s.github_owner.lower():
                route = "user/repos"
            else:
                owner = self.call("GET", f"users/{s.github_owner}")
                if owner.get("type") != "Organization":
                    raise PipelineError("Impossible de créer un dépôt pour un autre utilisateur")
                route = f"orgs/{s.github_owner}/repos"
            repo = self.call(
                "POST",
                route,
                json={
                    "name": s.github_repo,
                    "private": False,
                    "auto_init": True,
                    "description": "Couverture communale RGE et logements — données et méthode Legibat",
                    "homepage": s.methodology_url,
                },
            )
        if repo.get("private"):
            raise PipelineError(
                "Le dépôt existant est privé ; aucune modification de visibilité automatique"
            )
        if repo.get("archived"):
            raise PipelineError("Le dépôt GitHub est archivé")
        return repo

    def sync(self, root: Path, files, message):
        s = self.settings
        branch = quote(s.github_branch, safe="/")
        # Validation de l'ensemble avant tout upload de blob.
        contents = {}
        for path in files:
            resolved = path.resolve()
            if not resolved.is_relative_to(root.resolve()):
                raise PipelineError("Un fichier sort du répertoire de publication")
            relative = resolved.relative_to(root.resolve()).as_posix()
            if any(p in {".env", ".git", ".state", "work", ".venv"} for p in Path(relative).parts):
                raise PipelineError("Tentative de publier un fichier privé")
            content = resolved.read_bytes()
            if len(content) > 95 * 1024 * 1024:
                raise PipelineError(f"Fichier trop volumineux pour GitHub : {relative}")
            contents[relative] = content
        if s.github_transport == "git":
            from github_git_transport import sync_with_git

            return sync_with_git(self, root, contents, message)
        try:
            ref = self.call("GET", f"{self.repo_path}/git/ref/heads/{branch}")
        except ApiError as exc:
            if exc.status not in {404, 409}:
                raise
            repo = self.call("GET", self.repo_path)
            if repo.get("size", 0) == 0:
                # Initialise aussi un dépôt existant totalement vide.
                self.call(
                    "PUT",
                    f"{self.repo_path}/contents/.nojekyll",
                    json={
                        "message": "Initialiser le dépôt Open Data",
                        "content": "",
                        "branch": s.github_branch,
                    },
                )
            else:
                default_ref = self.call(
                    "GET",
                    f"{self.repo_path}/git/ref/heads/{quote(repo['default_branch'], safe='/')}",
                )
                self.call(
                    "POST",
                    f"{self.repo_path}/git/refs",
                    json={
                        "ref": f"refs/heads/{s.github_branch}",
                        "sha": default_ref["object"]["sha"],
                    },
                )
            ref = self.call("GET", f"{self.repo_path}/git/ref/heads/{branch}")
        head = ref["object"]["sha"]
        commit = self.call("GET", f"{self.repo_path}/git/commits/{head}")
        tree = self.call(
            "GET", f"{self.repo_path}/git/trees/{commit['tree']['sha']}", params={"recursive": "1"}
        )
        if tree.get("truncated"):
            raise PipelineError("Arbre GitHub tronqué : synchronisation arrêtée")
        previous = {f["path"]: f["sha"] for f in tree["tree"] if f["type"] == "blob"}
        entries = []
        for relative, content in sorted(contents.items()):
            if previous.get(relative) == git_blob_sha(content):
                continue
            blob = self.call(
                "POST",
                f"{self.repo_path}/git/blobs",
                json={"content": base64.b64encode(content).decode(), "encoding": "base64"},
            )
            entries.append({"path": relative, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        if not entries:
            return {"sha": head, "changed_files": 0}
        new_tree = self.call(
            "POST",
            f"{self.repo_path}/git/trees",
            json={"base_tree": commit["tree"]["sha"], "tree": entries},
        )
        new_commit = self.call(
            "POST",
            f"{self.repo_path}/git/commits",
            json={"message": message, "tree": new_tree["sha"], "parents": [head]},
        )
        # Un changement concurrent provoque 409/422. Pas de force push ni de perte de commit.
        self.call(
            "PATCH",
            f"{self.repo_path}/git/refs/heads/{branch}",
            json={"sha": new_commit["sha"], "force": False},
        )
        return {"sha": new_commit["sha"], "changed_files": len(entries)}

    def enable_pages(self):
        source = {"branch": self.settings.github_branch, "path": "/docs"}
        try:
            pages = self.call("GET", f"{self.repo_path}/pages")
        except ApiError as exc:
            if exc.status != 404:
                raise
            pages = self.call(
                "POST", f"{self.repo_path}/pages", json={"build_type": "legacy", "source": source}
            )
        else:
            if pages.get("source") != source or pages.get("build_type") != "legacy":
                self.call(
                    "PUT",
                    f"{self.repo_path}/pages",
                    json={"build_type": "legacy", "source": source},
                )
                pages = self.call("GET", f"{self.repo_path}/pages")
        # Ne pas annoncer un déploiement terminé : un build Pages peut encore être en attente.
        return {"url": pages.get("html_url"), "status": pages.get("status", "pending")}


def source_files(root: Path):
    """Liste blanche explicite : aucun parcours arbitraire de .env, logs ou caches."""
    names = [
        "main.py",
        "config.py",
        "common.py",
        "dataset_builder.py",
        "datagouv_client.py",
        "github_publisher.py",
        "github_git_transport.py",
        "landing_page_generator.py",
        "metadata.py",
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-lock.txt",
        "pyproject.toml",
        ".env.example",
        ".gitignore",
        "GUIDE.md",
        "LICENSE",
        "DATA_LICENSE.md",
        "SOURCES.md",
    ]
    files = [root / n for n in names if (root / n).is_file()]
    files += sorted((root / "tests").glob("test_*.py"))
    # Le workflow est livré localement. Installation séparée documentée : le jeton
    # d'exploitation n'a pas à disposer du droit Workflows pour publier des données.
    return files
