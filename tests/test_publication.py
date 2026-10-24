import json
from datetime import date

import httpx
import pytest

from common import Http, PipelineError, State
from config import Settings
from datagouv_client import DataGouvClient
from github_publisher import GitHubPublisher, git_blob_sha


def test_organization_creation_uses_siret_and_image_url_then_reuses_state(tmp_path):
    settings = Settings(
        _env_file=None,
        organization_siren="123456789",
        organization_siret="12345678900012",
        organization_logo_url="https://example.org/logo.svg",
    )
    created = []
    organization = {
        "id": "org",
        "business_number_id": settings.organization_siret,
        "permissions": {"edit": True},
    }

    def handle(request):
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["business_number_id"] == "12345678900012"
            assert body["image_url"] == settings.organization_logo_url
            assert "logo" not in body
            created.append(body)
            return httpx.Response(201, json={**organization, "permissions": {"edit": False}})
        if request.url.path.endswith("/organizations/org/"):
            return httpx.Response(200, json=organization)
        assert request.url.params["business_number_id"] == "12345678900012"
        return httpx.Response(200, json={"data": [], "next_page": None})

    http = Http(transport=httpx.MockTransport(handle))
    state_path = tmp_path / "state.json"
    client = DataGouvClient(settings, http, State(state_path, "test"))
    assert client.ensure_organization()["id"] == "org"
    resumed = DataGouvClient(settings, http, State(state_path, "test"))
    assert resumed.ensure_organization()["id"] == "org"
    assert len(created) == 1
    http.close()


def test_organization_siret_must_match_siren():
    with pytest.raises(ValueError, match="SIRET doit commencer"):
        Settings(
            _env_file=None,
            organization_siren="123456789",
            organization_siret="98765432100012",
        )


def test_datagouv_updates_same_resources_after_restart(tmp_path):
    dataset = {"id": "a" * 24, "resources": []}
    uploads = []

    def handle(request):
        path = request.url.path
        if request.method == "GET":
            return httpx.Response(200, json=dataset)
        if path.endswith("/upload/"):
            uploads.append(path)
            filename = (
                "schema.json" if b'filename="schema.json"' in request.content else "table.csv"
            )
            r = {
                "id": str(len(dataset["resources"]) + 1),
                "url": "https://static.data.gouv.fr/" + filename,
                "title": filename,
            }
            dataset["resources"].append(r)
            return httpx.Response(201, json=r)
        body = json.loads(request.content)
        if request.method == "PUT":
            resource_id = path.strip("/").split("/")[-1]
            r = next(r for r in dataset["resources"] if r["id"] == resource_id)
            r.update(body)
        else:
            r = {**body, "id": str(len(dataset["resources"]) + 1)}
            dataset["resources"].append(r)
        return httpx.Response(200, json=r)

    s = Settings(_env_file=None, as_of=date(2026, 10, 24))
    schema, csv = tmp_path / "schema.json", tmp_path / "table.csv"
    schema.write_text('{"fields":[]}', encoding="utf8")
    csv.write_text("code\n01001\n", encoding="utf8")
    http = Http(transport=httpx.MockTransport(handle))
    state = State(tmp_path / "state.json", "test")
    client = DataGouvClient(s, http, state)
    client.upload_resources(dataset, [schema, csv])
    DataGouvClient(s, http, State(tmp_path / "state.json", "test")).upload_resources(
        dataset, [schema, csv]
    )
    assert len(uploads) == 2
    assert all(path == f"/api/1/datasets/{dataset['id']}/upload/" for path in uploads)
    assert len(dataset["resources"]) == 3
    table = next(r for r in dataset["resources"] if r["title"] == "table.csv")
    assert table["schema"]["url"].endswith("schema.json")
    csv.write_text("code\n01002\n", encoding="utf8")
    client.upload_resources(dataset, [schema, csv])
    assert uploads[-1].endswith(f"/resources/{table['id']}/upload/")
    http.close()


def test_dataset_discovery_scoped_to_org_and_not_recreated(tmp_path):
    calls = []
    s = Settings(_env_file=None, as_of=date(2026, 10, 24))

    def handle(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("organizations/org/datasets/"):
            return httpx.Response(
                200, json={"data": [{"id": "existing", "title": s.title}], "next_page": None}
            )
        if request.method == "GET":
            return httpx.Response(
                200,
                json={"id": "existing", "organization": {"id": "org"}, "extras": {"retain": "yes"}},
            )
        payload = json.loads(request.content)
        assert payload["license"] == "lov2"
        assert payload["frequency"] == "weekly"
        assert payload["spatial"]["granularity"] == "fr:commune"
        assert payload["extras"]["retain"] == "yes"
        return httpx.Response(200, json={"id": "existing", **payload})

    http = Http(transport=httpx.MockTransport(handle))
    client = DataGouvClient(s, http, State(tmp_path / "state.json", "test"))
    assert client.upsert_dataset({"id": "org"}, "description")["id"] == "existing"
    assert not any(m == "POST" for m, _ in calls)
    http.close()


def test_github_tree_preserves_other_files_and_nonforced_commit(tmp_path):
    file = tmp_path / "dataset.csv"
    file.write_text("a\n1\n", encoding="utf8")
    calls = []

    def handle(request):
        path = request.url.path
        payload = json.loads(request.content) if request.content else {}
        calls.append((request.method, path, payload))
        if "/git/ref/" in path:
            return httpx.Response(200, json={"object": {"sha": "head"}})
        if path.endswith("/git/commits/head"):
            return httpx.Response(200, json={"tree": {"sha": "old-tree"}})
        if request.method == "GET" and "/git/trees/" in path:
            return httpx.Response(
                200,
                json={
                    "tree": [{"path": "unrelated.md", "sha": "keep", "type": "blob"}],
                    "truncated": False,
                },
            )
        if path.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": "blob"})
        if path.endswith("/git/trees"):
            assert payload["base_tree"] == "old-tree"
            assert payload["tree"] == [
                {"path": "dataset.csv", "mode": "100644", "type": "blob", "sha": "blob"}
            ]
            return httpx.Response(201, json={"sha": "new-tree"})
        if path.endswith("/git/commits"):
            assert payload["parents"] == ["head"]
            return httpx.Response(201, json={"sha": "new-commit"})
        if request.method == "PATCH":
            assert payload == {"sha": "new-commit", "force": False}
            return httpx.Response(200, json={})
        raise AssertionError(path)

    s = Settings(_env_file=None, github_owner="owner")
    http = Http(transport=httpx.MockTransport(handle))
    result = GitHubPublisher(s, http).sync(tmp_path, [file], "update")
    assert result == {"sha": "new-commit", "changed_files": 1}
    assert sum(m == "PATCH" for m, _, _ in calls) == 1
    http.close()


def test_github_unchanged_content_has_no_write(tmp_path):
    file = tmp_path / "x.txt"
    file.write_bytes(b"same")

    def handle(request):
        assert request.method == "GET"
        if "/git/ref/" in request.url.path:
            data = {"object": {"sha": "head"}}
        elif "/git/commits/" in request.url.path:
            data = {"tree": {"sha": "tree"}}
        else:
            data = {
                "tree": [{"path": "x.txt", "type": "blob", "sha": git_blob_sha(b"same")}],
                "truncated": False,
            }
        return httpx.Response(200, json=data)

    http = Http(transport=httpx.MockTransport(handle))
    result = GitHubPublisher(Settings(_env_file=None, github_owner="owner"), http).sync(
        tmp_path, [file], "update"
    )
    assert result["changed_files"] == 0
    http.close()


def test_github_secrets_and_private_repos_rejected(tmp_path):
    file = tmp_path / ".env"
    file.write_text("SECRET=x", encoding="utf8")
    http = Http(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"private": True}))
    )
    client = GitHubPublisher(Settings(_env_file=None, github_owner="owner"), http)
    with pytest.raises(PipelineError, match="privé"):
        client.sync(tmp_path, [file], "update")
    with pytest.raises(PipelineError, match="privé"):
        client.ensure_repository()
    http.close()
