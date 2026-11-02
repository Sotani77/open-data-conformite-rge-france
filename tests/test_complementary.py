import json
from datetime import date

import httpx
import pandas as pd
from frictionless import Package, Resource

from common import Http, State
from complementary_publications import ABSENCE, QUALIFICATIONS, build_topics, render_topics
from config import Settings
from datagouv_client import DataGouvClient
from dataset_builder import FAMILIES, build_dataset, export_dataset, sample_inputs


def build(tmp_path):
    settings = Settings(_env_file=None, as_of=date(2026, 11, 2), github_owner="owner")
    result = build_dataset(*sample_inputs(settings), settings)
    export_dataset(result, tmp_path, settings)
    return settings, result, build_topics(result, tmp_path, settings)


def test_zero_establishment_selection_does_not_depend_on_housing(tmp_path):
    settings, result, _ = build(tmp_path)
    row = result.frame.loc[result.frame.nb_etablissements_rge == 0].index[0]
    result.frame.loc[row, "nb_logements"] = pd.NA
    topics = build_topics(result, tmp_path, settings)
    selected = next(t for t in topics if t.slug == ABSENCE)
    assert result.frame.loc[row, "code_insee"] in set(selected.frame.code_insee)
    assert (selected.frame.nb_etablissements_rge == 0).all()
    assert selected.summary["nb_communes_logements_manquants"] >= 1
    assert (
        selected.frame.loc[
            selected.frame.code_insee == result.frame.loc[row, "code_insee"], "nb_logements"
        ]
        .isna()
        .all()
    )


def test_family_totals_preserve_overlaps_and_keep_zero_rows(tmp_path):
    _, result, topics = build(tmp_path)
    topic = next(t for t in topics if t.slug == QUALIFICATIONS)
    assert len(topic.frame) == len(result.departments) * len(FAMILIES)
    assert not topic.frame.duplicated(["code_departement", "famille"]).any()
    for family in FAMILIES:
        subset = topic.frame.loc[topic.frame.famille == family]
        assert int(subset.nb_qualifications.sum()) == int(result.frame["nb_qualif_" + family].sum())
        assert int(subset.nb_communes_avec_qualification.sum()) == int(
            (result.frame["nb_qualif_" + family] > 0).sum()
        )
    assert topic.frame.part_communes_avec_qualification_pct.between(0, 100).all()


def test_exports_are_schema_valid_and_pages_have_no_invented_dataset_id(tmp_path):
    settings, _, topics = build(tmp_path)
    files = render_topics(topics, tmp_path, settings, {})
    assert files
    for topic in topics:
        folder = tmp_path / "publications" / topic.slug
        package = Package(str(folder / "datapackage.json"))
        assert not list(Package.metadata_validate(package.to_descriptor()))
        csv = Resource(
            {
                "name": topic.slug,
                "path": topic.files[0].name,
                "schema": topic.schema,
                "dialect": {"delimiter": ";"},
            },
            basepath=str(folder),
        )
        assert csv.validate().valid
        assert pd.read_parquet(topic.files[1]).equals(topic.frame)
        data = json.loads((folder / "dataset.jsonld").read_text("utf8"))
        assert "sameAs" not in data
        assert "Publication en préparation" in (
            tmp_path / "docs" / topic.slug / "index.html"
        ).read_text("utf8")


def test_topic_dataset_ids_never_reuse_main_dataset_id(tmp_path):
    settings = Settings(_env_file=None, as_of=date(2026, 11, 2), datagouv_dataset_id="main-id")
    state = State(tmp_path / "state.json", "test")
    state.data["datasets"] = {"2026": "main-id"}
    writes = []

    def respond(request):
        if request.method == "GET":
            assert request.url.path.endswith("organizations/org/datasets/")
            return httpx.Response(200, json={"data": [], "next_page": None})
        payload = json.loads(request.content)
        assert payload["spatial"]["granularity"] == "fr:departement"
        writes.append(payload)
        return httpx.Response(201, json={"id": "topic-id", **payload})

    http = Http(transport=httpx.MockTransport(respond))
    client = DataGouvClient(settings, http, state)
    client.upsert_dataset(
        {"id": "org"},
        "description",
        key="topic-2026",
        title="New topic",
        granularity="fr:departement",
    )
    assert state.data["datasets"] == {"2026": "main-id", "topic-2026": "topic-id"}
    assert writes[0]["extras"]["legibat_pipeline_key"] == "topic-2026"
    http.close()
