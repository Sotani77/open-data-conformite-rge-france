from datetime import date

import pandas as pd
import pytest

from common import PipelineError
from config import Settings
from dataset_builder import (
    Geography,
    build_dataset,
    domain_families,
    load_housing,
    sample_inputs,
    tension,
)


def settings(**kwargs):
    return Settings(_env_file=None, as_of=date(2026, 10, 24), **kwargs)


def row(siret="12345678900001", **extra):
    return {
        "siret": siret,
        "code_insee": "01001",
        "lien_date_debut": "2026-01-01",
        "lien_date_fin": "2026-10-24",
        "domaine": "Pompe à chaleur : chauffage",
        "meta_domaine": "Travaux",
        "organisme": "qualitenr",
        "code_qualification": "Q1",
        **extra,
    }


def build(rows, **kwargs):
    s = settings(**kwargs)
    _, communes, regions, homes, provenance = sample_inputs(s)
    provenance.update(mode="live", partial=False)
    return build_dataset(rows, communes, regions, homes, provenance, s)


def test_dedup_siren_siret_qualification_and_active_boundaries():
    result = build(
        [
            row(),
            row(),
            row(siret="12345678900002"),
            row(siret="99999999900001", lien_date_fin="2026-10-23"),
            row(siret="88888888800001", lien_date_debut="2026-10-24"),
        ]
    )
    r = result.frame.iloc[0]
    assert r.nb_entreprises_rge == 2
    assert r.nb_etablissements_rge == 3
    assert r.nb_qualif_pac == 3
    assert r.nb_qualif_chauffage == 3
    assert r.ratio_artisans_1000_logements == 5
    assert r.indice_tension_rge == "Forte couverture"


def test_ambiguous_postcode_not_assigned_to_multiple_communes():
    communes = [
        {"code": "01001", "nom": "Saint-A", "codesPostaux": ["01000"]},
        {"code": "01002", "nom": "Saint-B", "codesPostaux": ["01000"]},
    ]
    geo = Geography(communes)
    assert geo.match({"code_postal": "01000", "commune": "ST A"}) == ("01001", "postal_nom")
    assert geo.match({"code_postal": "01000", "commune": "Aucune"}) == (None, "postal_ambigu")
    assert geo.match({"code_insee": "99999", "code_postal": "01000", "commune": "Saint-A"}) == (
        None,
        "insee_inconnu",
    )


@pytest.mark.parametrize(
    "code,parent", [("75116", "75056"), ("69382", "69123"), ("13216", "13055")]
)
def test_arrondissements(code, parent):
    geo = Geography([{"code": parent, "nom": "Ville", "codesPostaux": []}])
    assert geo.match({"code_insee": code})[0] == parent


def test_studies_are_excluded_and_pv_does_not_include_solar_heating():
    result = build([row(), row(siret="11111111100001", domaine="Etude solaire photovoltaïque")])
    assert result.quality["counts"]["matched_establishments"] == 1
    assert domain_families("Chauffage et/ou eau chaude solaire") == {"chauffage"}
    assert domain_families("Panneaux solaires photovoltaïques") == {"pv"}


@pytest.mark.parametrize(
    "n,housing,label",
    [
        (0, 100, "Désert RGE"),
        (1, 1000, "Sous-capacité"),
        (2, 1000, "Équilibré"),
        (5, 1000, "Forte couverture"),
        (0, None, "Non calculable"),
        (1, 0, "Non calculable"),
    ],
)
def test_thresholds_and_missing_denominator(n, housing, label):
    assert tension(n, housing, settings()) == label


def test_conflicting_siret_blocks_publication():
    result = build([row(), row(code_insee="75056")])
    assert not result.quality["publishable"]
    assert result.quality["counts"]["conflicting_establishments"] == 1
    assert result.frame.nb_etablissements_rge.sum() == 0


def test_missing_dates_are_not_treated_as_active():
    result = build([row(lien_date_fin=""), row(siret="BAD")])
    assert result.quality["counts"]["invalid_dates"] == 1
    assert result.quality["counts"]["invalid_siret"] == 1
    assert not result.quality["publishable"]


def test_region_unique_company_counts_are_not_commune_sums():
    result = build([row(), row(siret="12345678900002", code_insee="01002")])
    assert result.frame.nb_entreprises_rge.sum() == 2
    region = next(r for r in result.regions if r["code_region"] == "84")
    assert region["nb_entreprises_rge"] == 1
    assert region["nb_etablissements_rge"] == 2


def test_housing_long_format_filters_geography_year_and_measure(tmp_path):
    data = []
    for geo_type, year, measure, value in [
        ("COM", "2023", "DWELLINGS", 400.5),
        ("ARM", "2023", "DWELLINGS", 500),
        ("COM", "2017", "DWELLINGS", 300),
        ("COM", "2023", "DWELLINGS_OCS_DW_MAIN", 200),
    ]:
        data.append(
            {
                "GEO": "01001",
                "GEO_OBJECT": geo_type,
                "TIME_PERIOD": year,
                "GEO_REF": "2026",
                "DS": "DS_RP_LOGEMENT_PRINC",
                "TAB_MEASURE": measure,
                "OBS_VALUE": value,
                "UNIT_MULT": None,
                "CONF_STATUS": None,
            }
        )
    path = tmp_path / "insee.parquet"
    pd.DataFrame(data).to_parquet(path)
    assert load_housing(path, settings()) == {"01001": 400.5}


def test_duplicate_housing_is_error(tmp_path):
    path = tmp_path / "housing.csv"
    path.write_text("code_insee;nb_logements\n01001;1\n01001;2\n", encoding="utf8")
    with pytest.raises(PipelineError, match="Plusieurs"):
        load_housing(path, settings())


def test_estimate_is_flagged_and_has_no_insee_year():
    result = build([row()], housing_mode="estimate")
    assert set(result.frame.source_logements) == {"estimation_population"}
    assert result.frame.millesime_logements.isna().all()


def test_sample_never_publishable():
    s = settings()
    result = build_dataset(*sample_inputs(s), s)
    assert not result.quality["publishable"]
    assert set(result.frame.indice_tension_rge) == {"Échantillon"}
