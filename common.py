"""I/O atomiques et HTTP : reprise des lectures, jamais de répétition aveugle des créations."""

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

LOG = logging.getLogger(__name__)


class PipelineError(RuntimeError):
    pass


class ApiError(PipelineError):
    def __init__(self, status, method, url):
        self.status = status
        # Aucun corps de réponse, query string ou en-tête secret dans les logs.
        p = urlsplit(str(url))
        super().__init__(f"HTTP {status} : {method} {p.scheme}://{p.netloc}{p.path}")


def write_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    tmp.replace(path)


def write_json(path: Path, data):
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def sha256(path: Path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


class Http:
    def __init__(self, timeout=60, attempts=4, headers=None, transport=None, sleeper=time.sleep):
        self.attempts = attempts
        self.sleep = sleeper
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": "Legibat-OpenData/1.0", **(headers or {})},
        )

    def close(self):
        self.client.close()

    def request(self, method, url, **kwargs):
        retryable = method.upper() in {"GET", "HEAD", "PUT"} and "files" not in kwargs
        attempts = self.attempts if retryable else 1
        for attempt in range(attempts):
            try:
                res = self.client.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                if attempt + 1 == attempts:
                    suffix = (
                        " Résultat d'écriture incertain : relancer pour réconciliation."
                        if not retryable
                        else ""
                    )
                    raise PipelineError(f"Échec réseau ({type(exc).__name__}).{suffix}") from None
                self.sleep(min(2**attempt, 30))
                continue
            rate_limited = res.status_code == 429 or (
                res.status_code == 403
                and (
                    "retry-after" in res.headers or res.headers.get("x-ratelimit-remaining") == "0"
                )
            )
            if (rate_limited or res.status_code in {500, 502, 503, 504}) and attempt + 1 < attempts:
                delay = 2**attempt
                retry_after = res.headers.get("retry-after", "")
                try:
                    delay = float(retry_after)
                except ValueError:
                    if retry_after:
                        try:
                            delay = (
                                parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)
                            ).total_seconds()
                        except (ValueError, TypeError):
                            pass
                if delay > 60:
                    raise PipelineError("Quota API atteint ; relancer après le délai Retry-After.")
                self.sleep(max(0, delay))
                continue
            if res.is_error or res.is_redirect:
                raise ApiError(res.status_code, method, url)
            return res
        raise PipelineError("Nombre de tentatives HTTP épuisé")

    def json(self, method, url, **kwargs):
        res = self.request(method, url, **kwargs)
        return res.json() if res.content else {}


class Api:
    def __init__(self, base_url, http):
        self.base_url = base_url.rstrip("/") + "/"
        self.http = http

    def url(self, path):
        target = urljoin(self.base_url, path)
        b, t = urlsplit(self.base_url), urlsplit(target)
        if (b.scheme, b.netloc) != (t.scheme, t.netloc) or not t.path.startswith(b.path):
            raise PipelineError("L'API a renvoyé un lien hors de son origine autorisée")
        return target

    def call(self, method, path, **kwargs):
        return self.http.json(method, self.url(path), **kwargs)


class State:
    def __init__(self, path: Path, namespace: str):
        self.path = path
        self.all = json.loads(path.read_text("utf-8")) if path.exists() else {}
        self.data = self.all.setdefault(namespace, {})

    def save(self):
        write_json(self.path, self.all)
