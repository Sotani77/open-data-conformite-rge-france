import httpx
import pytest

from common import Http, PipelineError
from verify_publication import check_download


def test_public_download_detects_stale_content_even_with_http_200(tmp_path):
    path = tmp_path / "data.csv"
    path.write_bytes(b"new data")
    http = Http(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"old data")))
    with pytest.raises(PipelineError, match="Contenu public différent"):
        check_download(http, "https://example.org/data.csv", path)
    http.close()


def test_public_download_checks_exact_bytes_without_credentials(tmp_path):
    path = tmp_path / "data.csv"
    path.write_bytes(b"new data")

    def respond(request):
        assert "authorization" not in request.headers
        assert "x-api-key" not in request.headers
        return httpx.Response(200, content=path.read_bytes())

    http = Http(transport=httpx.MockTransport(respond))
    assert check_download(http, "https://example.org/data.csv", path)["status"] == 200
    http.close()
