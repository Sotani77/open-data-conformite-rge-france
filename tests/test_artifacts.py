import json
from datetime import date

import pandas as pd
from frictionless import Package, Resource

from common import Http
from config import Settings
from dataset_builder import build_dataset, export_dataset, sample_inputs
from landing_page_generator import official_iframe, safe_jsonld
from main import main
from metadata import description, generate_metadata, jsonld


def test_export_schema_parquet_and_datapackage(tmp_path):
    s = Settings(_env_file=None, as_of=date(2026, 10, 24))
    result = build_dataset(*sample_inputs(s), s)
    files = export_dataset(result, tmp_path, s)
    generate_metadata(result, tmp_path, s, files, {})
    package = Package(str(tmp_path / "datapackage.json"))
    assert not list(Package.metadata_validate(package.to_descriptor()))
    for suffix, delimiter in [(".csv", ";"), ("-comma.csv", ",")]:
        resource = Resource(
            {
                "name": "communes",
                "path": s.stem + suffix,
                "schema": result.schema,
                "dialect": {"delimiter": delimiter},
            },
            basepath=str(tmp_path / "dist_data"),
        )
        assert resource.validate().valid
    parquet = pd.read_parquet(tmp_path / "dist_data" / (s.stem + ".parquet"))
    assert list(parquet.code_insee) == ["01001", "01002", "2A004", "75056"]
    assert parquet.equals(result.frame)
    assert len(description(result, s).split()) > 600


def test_no_fake_publication_or_iframe_and_safe_script():
    s = Settings(_env_file=None, as_of=date(2026, 10, 24))
    result = build_dataset(*sample_inputs(s), s)
    assert "sameAs" not in jsonld(result, s, {})
    assert "iframe" not in official_iframe({}, s.datagouv_api_url)
    encoded = safe_jsonld({"name": "</script><script>alert(1)</script>"})
    assert "</script>" not in encoded
    assert json.loads(encoded)["name"].startswith("</script>")
    iframe = official_iframe(
        {
            "dataset_id": "a" * 24,
            "resources": [{"format": "csv", "preview_url": "https://evil.test/x"}],
        },
        s.datagouv_api_url,
    )
    assert "evil.test" not in iframe
    assert "/embeds/datasets/" in iframe


def test_sample_dry_run_performs_no_network(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Le mode sample doit être hors ligne")

    monkeypatch.setattr(Http, "request", denied)
    assert (
        main(
            ["--dry-run", "--source", "sample", "--output", str(tmp_path), "--as-of", "2026-10-24"]
        )
        == 0
    )
    assert (tmp_path / "docs/index.html").exists()
    assert (tmp_path / "landing/legibat-fragment.html").exists()
    assert json.loads((tmp_path / "publication.json").read_text("utf8"))["status"] == "local"
    assert main(["--publish", "--source", "sample", "--output", str(tmp_path)]) == 1
