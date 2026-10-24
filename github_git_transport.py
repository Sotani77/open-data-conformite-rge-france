"""Optional compressed Git transport; credentials never enter URLs or command lines."""

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from common import PipelineError

LOG = logging.getLogger(__name__)


def sync_with_git(publisher, root: Path, contents: dict, message: str):
    settings = publisher.settings
    git = shutil.which("git")
    if not git:
        raise PipelineError("GITHUB_TRANSPORT=git nécessite Git installé")
    if settings.github_api_url.rstrip("/") != "https://api.github.com":
        raise PipelineError("Le transport Git configuré prend en charge github.com")
    user = publisher.preflight()
    workspace = (root / "work").resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    # A fresh private clone preserves remote files and avoids touching a user's checkout.
    task = Path(tempfile.mkdtemp(prefix="github-publication-", dir=workspace))
    checkout = task / "repository"
    helper = task / "askpass.py"
    helper.write_text(
        "import os, sys\n"
        "print('x-access-token' if 'username' in sys.argv[1].lower() "
        "else os.environ['RGE_GIT_TOKEN'])\n",
        encoding="utf8",
    )
    if os.name == "nt":
        launcher = task / "askpass.cmd"
        launcher.write_text(
            '@echo off\n"%RGE_GIT_PYTHON%" "%RGE_GIT_HELPER%" "%~1"\n', encoding="utf8"
        )
    else:
        launcher = task / "askpass.sh"
        launcher.write_text(
            '#!/bin/sh\nexec "$RGE_GIT_PYTHON" "$RGE_GIT_HELPER" "$1"\n', encoding="utf8"
        )
        launcher.chmod(0o700)
    token = settings.github_token.get_secret_value()
    env = {
        **os.environ,
        "GIT_ASKPASS": str(launcher),
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "never",
        "RGE_GIT_TOKEN": token,
        "RGE_GIT_PYTHON": sys.executable,
        "RGE_GIT_HELPER": str(helper),
    }

    def run(*args, cwd=None, allowed=(0,)):
        try:
            result = subprocess.run(
                [git, "-c", "credential.helper=", "-c", "core.autocrlf=false", *args],
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf8",
                errors="replace",
                timeout=600,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise PipelineError("Transfert Git expiré ; relancer pour réconciliation") from None
        if result.returncode not in allowed:
            detail = result.stderr.replace(token, "[secret]")[-1500:]
            raise PipelineError("Échec Git : " + detail)
        return result

    remote = f"https://github.com/{settings.github_owner}/{settings.github_repo}.git"
    LOG.info("Préparation d'un transfert Git compressé")
    run("clone", "--quiet", "--no-tags", remote, str(checkout))
    remote_branch = "refs/remotes/origin/" + settings.github_branch
    exists = run("show-ref", "--verify", "--quiet", remote_branch, cwd=checkout, allowed=(0, 1))
    if exists.returncode == 0:
        run("checkout", "--quiet", settings.github_branch, cwd=checkout)
    else:
        run("checkout", "--quiet", "-b", settings.github_branch, cwd=checkout)
    for relative, content in contents.items():
        target = checkout / relative
        if not target.resolve().is_relative_to(checkout.resolve()):
            raise PipelineError("Chemin sortant du clone Git")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    run("add", "--", *contents.keys(), cwd=checkout)
    changed = run("diff", "--cached", "--name-only", cwd=checkout).stdout.splitlines()
    if not changed:
        return {"sha": run("rev-parse", "HEAD", cwd=checkout).stdout.strip(), "changed_files": 0}
    email = f"{user['id']}+{user['login']}@users.noreply.github.com"
    run(
        "-c",
        "user.name=" + user["login"],
        "-c",
        "user.email=" + email,
        "commit",
        "--quiet",
        "-m",
        message,
        cwd=checkout,
    )
    LOG.info("Transfert Git compressé : %s fichiers modifiés", len(changed))
    # No force: a concurrent remote update rejects the push instead of losing commits.
    run("push", "--porcelain", "origin", "HEAD:refs/heads/" + settings.github_branch, cwd=checkout)
    return {
        "sha": run("rev-parse", "HEAD", cwd=checkout).stdout.strip(),
        "changed_files": len(changed),
    }
