from datetime import date

import httpx
import pytest

from common import Api, ApiError, Http, PipelineError
from config import Settings
from dataset_builder import SELECT, fetch_live


def test_retry_reads_respect_retry_after():
    calls, sleeps = [], []

    def handle(request):
        calls.append(request)
        return (
            httpx.Response(429, headers={"Retry-After": "3"})
            if len(calls) == 1
            else httpx.Response(200, json={"ok": True})
        )

    http = Http(transport=httpx.MockTransport(handle), sleeper=sleeps.append)
    assert http.json("GET", "https://example.test/") == {"ok": True}
    assert sleeps == [3]
    http.close()


def test_post_not_retried_after_server_error():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(503)

    http = Http(transport=httpx.MockTransport(handle), sleeper=lambda _: None)
    with pytest.raises(ApiError):
        http.json("POST", "https://example.test/", json={})
    assert len(calls) == 1
    http.close()


def test_api_does_not_send_secret_to_external_pagination():
    http = Http(headers={"X-API-KEY": "test-secret"})
    api = Api("https://www.data.gouv.fr/api/1/", http)
    with pytest.raises(PipelineError, match="origine"):
        api.url("https://evil.test/collect")
    with pytest.raises(PipelineError):
        api.url("/different-path")
    http.close()


def collection_http(change=False, bad_next=False):
    metadata_calls = []

    def handle(request):
        url = str(request.url)
        if request.url.path.endswith("/lines"):
            if "after" not in request.url.params:
                return httpx.Response(
                    200,
                    json={
                        "total": 2,
                        "results": [{"_id": "a"}],
                        "next": "https://evil.test/lines"
                        if bad_next
                        else "https://data.ademe.fr/data-fair/api/v1/datasets/id/lines?after=1",
                    },
                )
            # Data Fair ne répète pas nécessairement total sur les pages suivantes.
            return httpx.Response(200, json={"results": [{"_id": "b"}]})
        if "datasets" in url:
            metadata_calls.append(url)
            return httpx.Response(
                200,
                json={
                    "schema": [{"key": k} for k in SELECT],
                    "dataUpdatedAt": "changed" if change and len(metadata_calls) > 1 else "stable",
                },
            )
        if request.url.path == "/communes":
            return httpx.Response(200, json=[{"code": "01001", "nom": "A"}])
        if request.url.path == "/regions":
            return httpx.Response(200, json=[])
        raise AssertionError(url)

    return Http(transport=httpx.MockTransport(handle))


def test_cursor_pagination_without_repeated_total(tmp_path):
    http = collection_http()
    s = Settings(_env_file=None, as_of=date(2026, 10, 24), housing_mode="estimate")
    rows, *_ = fetch_live(s, tmp_path, http)
    assert [r["_id"] for r in rows] == ["a", "b"]
    http.close()


@pytest.mark.parametrize("change,bad_next", [(True, False), (False, True)])
def test_changed_snapshot_or_external_next_is_rejected(tmp_path, change, bad_next):
    http = collection_http(change, bad_next)
    s = Settings(_env_file=None, housing_mode="estimate")
    with pytest.raises(PipelineError):
        fetch_live(s, tmp_path, http)
    http.close()
