"""Check public downloads, committed files and the completed Pages deployment."""

import argparse
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import httpx

from common import Http, PipelineError, write_json
from config import Settings
from github_publisher import GitHubPublisher, git_blob_sha, source_files


def check_download(http, url, path):
    response = http.request("GET", url)
    digest = hashlib.sha256(response.content).hexdigest()
    if digest != hashlib.sha256(path.read_bytes()).hexdigest():
        raise PipelineError(f"Contenu public différent : {path.name}")
    return {"file": path.name, "url": url, "status": response.status_code, "sha256": digest}


def verify(root, settings, wait_seconds):
    publication = json.loads((root / "publication.json").read_text("utf8"))
    if publication.get("status") != "published" or publication.get("dry_run"):
        raise PipelineError("Une publication réelle terminée est requise")
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "all_ok": False}
    output = root / "work/publication-verification.json"
    public = Http(timeout=120, attempts=4)
    # Public requests carry no credentials; resource/CDN redirects are expected.
    public.client.follow_redirects = True
    authenticated = Http(
        timeout=60,
        attempts=4,
        headers={
            "Authorization": "Bearer " + settings.github_token.get_secret_value(),
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": settings.github_api_version,
        },
    )
    gh = GitHubPublisher(settings, authenticated)
    try:
        dataset = public.request(
            "GET", settings.datagouv_api_url + "datasets/" + publication["dataset_id"] + "/"
        ).json()
        if dataset.get("private") is not False or dataset.get("frequency") != "weekly":
            raise PipelineError("Dataset non public ou fréquence hebdomadaire absente")
        public.request("GET", dataset["page"])
        public.request("GET", publication["github_url"])
        report["dataset_url"] = dataset["page"]
        report["downloads"] = []
        manifest = json.loads((root / "dist_data/manifest.json").read_text("utf8"))
        # An annual rollover can leave earlier editions in Git. Only this run's
        # manifest belongs to the current annual data.gouv.fr dataset.
        for name in sorted({*manifest, "manifest.json"}):
            if Path(name).name != name:
                raise PipelineError("Chemin invalide dans le manifeste")
            path = root / "dist_data" / name
            resources = [r for r in dataset["resources"] if r.get("title") == path.name]
            if len(resources) != 1:
                raise PipelineError(f"Ressource absente ou ambiguë : {path.name}")
            report["downloads"].append(check_download(public, resources[0]["url"], path))
        head = gh.call("GET", f"{gh.repo_path}/git/ref/heads/{settings.github_branch}")["object"][
            "sha"
        ]
        tree = gh.call("GET", f"{gh.repo_path}/git/trees/{head}", params={"recursive": "1"})
        if tree.get("truncated"):
            raise PipelineError("Arbre GitHub tronqué")
        remote = {f["path"]: f["sha"] for f in tree["tree"] if f["type"] == "blob"}
        forbidden = {".env", ".state", ".venv", "work", "__pycache__"}
        if any(forbidden.intersection(Path(path).parts) for path in remote):
            raise PipelineError("Un fichier privé figure dans le dépôt")
        files = source_files(root)
        files += [
            p
            for folder in ("dist_data", "docs", "landing")
            for p in (root / folder).rglob("*")
            if p.is_file()
        ]
        files += [
            root / name
            for name in (
                "README.md",
                "CITATION.cff",
                "DATASET_DESCRIPTION.md",
                "datapackage.json",
                "dataset.jsonld",
                "publication.json",
                ".github/workflows/weekly.yml",
            )
        ]
        for path in files:
            relative = path.relative_to(root).as_posix()
            if remote.get(relative) != git_blob_sha(path.read_bytes()):
                raise PipelineError(f"Fichier GitHub différent : {relative}")
        report.update(github_head=head, github_files_checked=len(files))
        deadline = time.monotonic() + wait_seconds
        while True:
            build = gh.call("GET", f"{gh.repo_path}/pages/builds/latest")
            if build.get("commit") == head and build.get("status") == "errored":
                raise PipelineError("Le déploiement GitHub Pages a échoué")
            if build.get("commit") == head and build.get("status") == "built":
                try:
                    report["pages"] = [
                        check_download(
                            public, urljoin(publication["pages_url"], rel), root / "docs" / local
                        )
                        for rel, local in (
                            ("", "index.html"),
                            ("assets/couverture-regions.svg", "assets/couverture-regions.svg"),
                        )
                    ]
                    break
                except PipelineError:
                    if time.monotonic() >= deadline:
                        raise
            if time.monotonic() >= deadline:
                raise PipelineError("GitHub Pages n'a pas déployé le commit attendu dans le délai")
            print("Déploiement Pages en cours ; nouvelle vérification dans 20 secondes", flush=True)
            time.sleep(20)
        report.update(all_ok=True, pages_build_commit=build["commit"], pages_status=build["status"])
        print(json.dumps(report, ensure_ascii=False), flush=True)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf8") as stream:
                stream.write(
                    f"## Publication vérifiée\n\n"
                    f"- [data.gouv.fr]({publication['dataset_url']})\n"
                    f"- [GitHub]({publication['github_url']})\n"
                    f"- [GitHub Pages]({publication['pages_url']})\n\n"
                    f"{len(report['downloads'])} téléchargements, {len(files)} fichiers GitHub "
                    f"et les empreintes du site contrôlés.\n"
                )
        return report
    finally:
        write_json(output, report)
        public.close()
        authenticated.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-seconds", type=int, default=600)
    args = parser.parse_args()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        verify(Path(__file__).resolve().parent, Settings(), args.wait_seconds)
    except (PipelineError, httpx.HTTPError, KeyError, ValueError) as exc:
        detail = str(exc) if isinstance(exc, PipelineError) else type(exc).__name__
        print(f"Échec de vérification : {detail}", flush=True)
        raise SystemExit(1) from None
