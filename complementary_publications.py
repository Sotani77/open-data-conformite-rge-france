"""Two reproducible thematic datasets derived from the validated RGE collection."""

import html
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from common import PipelineError, sha256, write_json, write_text
from datagouv_client import DataGouvClient
from dataset_builder import FAMILIES
from landing_page_generator import STYLE, safe_jsonld
from metadata import LICENSE_URL, format_fr

FAMILY_NAMES = {
    "pv": "Photovoltaïque",
    "pac": "Pompes à chaleur",
    "isolation": "Isolation",
    "chauffage": "Chauffage",
    "menuiserie": "Menuiserie",
}
ABSENCE = "communes-sans-etablissement-rge"
QUALIFICATIONS = "qualifications-rge-metiers-departements"


@dataclass
class Topic:
    slug: str
    title: str
    frame: pd.DataFrame
    schema: dict
    summary: dict
    explanation: str
    granularity: str
    files: list[Path]


def page_url(settings, slug):
    return f"https://{settings.github_owner.lower()}.github.io/{settings.github_repo}/{slug}/"


def known_sum(series):
    value = series.sum(min_count=1)
    return None if pd.isna(value) else float(value)


def build_topics(result, root, settings):
    frame = result.frame
    selected = frame.loc[frame.nb_etablissements_rge == 0].copy()
    columns = [
        "code_insee",
        "nom_commune",
        "code_departement",
        "code_region",
        "nom_region",
        "population",
        "nb_logements",
        "source_logements",
        "millesime_logements",
        "nb_entreprises_rge",
        "nb_etablissements_rge",
        "date_observation",
        "donnees_partielles",
    ]
    selected = selected[columns].sort_values("code_insee").reset_index(drop=True)
    departments = []
    for code, group in frame.groupby("code_departement", sort=True):
        zero = group.loc[group.nb_etablissements_rge == 0]
        departments.append(
            {
                "code_departement": str(code),
                "nb_communes_reference": len(group),
                "nb_communes_sans_etablissement_rge": len(zero),
                "part_communes_sans_etablissement_pct": round(100 * len(zero) / len(group), 6),
                "population_connue_communes_selectionnees": known_sum(zero.population),
                "logements_connus_communes_selectionnees": known_sum(zero.nb_logements),
                "nb_communes_population_manquante": int(zero.population.isna().sum()),
                "nb_communes_logements_manquants": int(zero.nb_logements.isna().sum()),
            }
        )
    absence_summary = {
        "nb_communes_reference": len(frame),
        "nb_communes_sans_etablissement_rge": len(selected),
        "part_communes_sans_etablissement_pct": round(100 * len(selected) / len(frame), 6),
        "population_connue_communes_selectionnees": known_sum(selected.population),
        "logements_connus_communes_selectionnees": known_sum(selected.nb_logements),
        "nb_communes_logements_manquants": int(selected.nb_logements.isna().sum()),
        "departements": departments,
    }
    absence_schema = {
        "fields": [f for f in result.schema["fields"] if f["name"] in columns],
        "primaryKey": "code_insee",
        "missingValues": [""],
    }
    topics = [
        Topic(
            ABSENCE,
            f"Communes sans établissement de travaux RGE actif recensé — {settings.year}",
            selected,
            absence_schema,
            absence_summary,
            "La sélection conserve toutes les communes du référentiel API Geo dont le nombre "
            "d'établissements de travaux RGE actifs rattachés est exactement nul. Elle inclut aussi "
            "les communes sans dénominateur logement connu : celles-ci restent identifiables dans "
            "les colonnes source_logements et nb_logements. Aucun effectif nul ne résulte du "
            "remplacement d'une valeur manquante. La synthèse fournit, pour chaque département, "
            "le nombre de communes de référence, le nombre sélectionné, leur part et les sommes "
            "de population et de logements connues. Ces sommes ne mesurent pas des personnes "
            "privées d'accès à un artisan. L'absence d'implantation recensée n'est ni une preuve "
            "d'absence de professionnels intervenant sur place, ni une mesure de pénurie.",
            "fr:commune",
            [],
        )
    ]
    records = []
    for department in result.departments:
        code = department["code_departement"]
        group = frame.loc[frame.code_departement == code]
        for family in FAMILIES:
            qualifications = int(department["nb_qualif_" + family])
            if qualifications != int(group["nb_qualif_" + family].sum()):
                raise PipelineError("Les qualifications communales et départementales divergent")
            covered = int((group["nb_qualif_" + family] > 0).sum())
            records.append(
                {
                    "code_departement": code,
                    "famille": family,
                    "famille_travaux": FAMILY_NAMES[family],
                    "nb_qualifications": qualifications,
                    "nb_communes_reference": len(group),
                    "nb_communes_avec_qualification": covered,
                    "part_communes_avec_qualification_pct": round(100 * covered / len(group), 6),
                    "date_observation": settings.as_of.isoformat(),
                    "donnees_partielles": bool(result.provenance["partial"]),
                }
            )
    qualifications_frame = pd.DataFrame(records)
    definitions = [
        ("code_departement", "string", "Code département ou territoire du référentiel API Geo."),
        (
            "famille",
            "string",
            "Identifiant de famille : pv, pac, isolation, chauffage, menuiserie.",
        ),
        ("famille_travaux", "string", "Libellé de la famille de travaux."),
        (
            "nb_qualifications",
            "integer",
            "Combinaisons distinctes établissement-organisme-code-domaine valides ; pas un nombre d'entreprises.",
        ),
        (
            "nb_communes_reference",
            "integer",
            "Nombre total de communes du département dans le référentiel utilisé.",
        ),
        (
            "nb_communes_avec_qualification",
            "integer",
            "Communes avec au moins une qualification de cette famille rattachée.",
        ),
        (
            "part_communes_avec_qualification_pct",
            "number",
            "100 × communes avec qualification / communes du référentiel départemental.",
        ),
        ("date_observation", "date", "Date de validité des qualifications et d'observation."),
        (
            "donnees_partielles",
            "boolean",
            "Vrai pour une démonstration, dont la publication est interdite.",
        ),
    ]
    schema = {
        "fields": [
            {"name": name, "type": kind, "description": detail, "constraints": {"required": True}}
            for name, kind, detail in definitions
        ],
        "primaryKey": ["code_departement", "famille"],
        "missingValues": [""],
    }
    totals = [
        {
            "famille": family,
            "famille_travaux": FAMILY_NAMES[family],
            "nb_qualifications": int(
                qualifications_frame.loc[
                    qualifications_frame.famille == family, "nb_qualifications"
                ].sum()
            ),
            "nb_communes_avec_qualification": int((frame["nb_qualif_" + family] > 0).sum()),
        }
        for family in FAMILIES
    ]
    topics.append(
        Topic(
            QUALIFICATIONS,
            f"Qualifications RGE actives par famille de travaux et département — {settings.year}",
            qualifications_frame,
            schema,
            {
                "nb_departements_ou_territoires": len(result.departments),
                "nb_familles": len(FAMILIES),
                "nb_lignes": len(records),
                "familles": totals,
            },
            "Ce jeu adopte un format long : une ligne par département ou territoire et par famille "
            "de travaux, y compris lorsque le nombre de qualifications est nul. Les cinq familles "
            "sont le photovoltaïque, les pompes à chaleur, l'isolation, le chauffage et la menuiserie. "
            "Une qualification est une combinaison distincte établissement, organisme, code de "
            "qualification et domaine normalisé. Plusieurs qualifications peuvent appartenir à "
            "un même établissement. Il ne faut donc pas interpréter ces effectifs comme des "
            "entreprises, des artisans ou des emplois. Les familles se chevauchent, notamment "
            "pompes à chaleur et chauffage, et leurs effectifs ne doivent pas être additionnés. "
            "Le taux de couverture compte les communes disposant d'au moins une qualification "
            "de la famille parmi toutes les communes du référentiel départemental ; il ne mesure "
            "ni une part de marché ni une disponibilité pour intervenir. Les domaines non classés "
            "dans ces cinq familles sont documentés dans le contrôle qualité de la source.",
            "fr:departement",
            [],
        )
    )
    source_manifest = root / "dist_data/manifest.json"
    for topic in topics:
        folder = root / "publications" / topic.slug
        folder.mkdir(parents=True, exist_ok=True)
        table_name = f"{topic.slug}-{settings.year}"
        csv, parquet = folder / (table_name + ".csv"), folder / (table_name + ".parquet")
        topic.frame.to_csv(csv, index=False, sep=";", encoding="utf8", lineterminator="\n")
        topic.frame.to_parquet(parquet, index=False)
        write_json(folder / "schema.json", topic.schema)
        write_json(folder / "synthese.json", topic.summary)
        write_json(
            folder / "provenance.json",
            {
                **result.provenance,
                "transformation": topic.slug,
                "transformation_version": "1.0.0",
                "source_manifest_sha256": sha256(source_manifest),
                "source_parquet_sha256": sha256(root / "dist_data" / (settings.stem + ".parquet")),
            },
        )
        write_json(
            folder / "quality-report.json",
            {
                "publishable": result.quality["publishable"],
                "rows": len(topic.frame),
                "source_quality": result.quality,
                "blocking_reasons": result.quality["blocking_reasons"],
            },
        )
        topic.files = [csv, parquet] + [
            folder / n
            for n in ("schema.json", "synthese.json", "provenance.json", "quality-report.json")
        ]
        write_json(
            folder / "manifest.json",
            {p.name: {"sha256": sha256(p), "bytes": p.stat().st_size} for p in topic.files},
        )
        topic.files.append(folder / "manifest.json")
    return topics


def topic_description(topic, settings, base_publication):
    source = base_publication.get("dataset_url") or settings.methodology_url
    return f"""## Objet et lecture

{topic.explanation}

## Observation et sources

Observation au **{settings.as_of.isoformat()}**. Ce jeu dérivé est produit par Legibat, organisme privé indépendant, à partir de la [cartographie communale source]({source}). La collecte complète utilise la [liste RGE de l'ADEME]({settings.ademe_dataset_url}), les [communes de l'API Geo]({settings.geo_api_url}/communes) et le [comparateur de territoires INSEE](https://www.insee.fr/fr/statistiques/2521169). Les logements correspondent au recensement {settings.housing_year}, géographie {settings.housing_geo_year}. Le millésime de population n'est pas fourni par l'API Geo utilisée ; seule sa date de collecte est attestée.

## Traitement reproductible

Les qualifications doivent être valides à la date d'observation. Les activités d'étude, d'audit et d'architecture sont exclues du périmètre travaux. Le rattachement communal utilise le code INSEE lorsqu'il est disponible, sinon le code postal et le nom normalisé de commune. Les correspondances ambiguës sont exclues, sans répartition artificielle entre plusieurs communes. Les établissements aux localisations contradictoires sont exclus de tous les agrégats. Les SIRET et les combinaisons de qualification sont dédupliqués avant calcul. Paris, Lyon et Marseille sont ramenés à la commune entière. Les territoires conservés sont ceux présents dans le référentiel API Geo ; la couverture du référentiel ne doit pas être confondue avec un inventaire exhaustif de tous les territoires français.

Les dénominateurs absents restent manquants. Les compteurs nuls désignent une absence de qualification ou d'établissement retenu après ces contrôles, et non une absence de service sur le terrain. Les contrôles de la source bloquent toute publication partielle ou de démonstration. Le fichier provenance.json conserve la date de collecte, les paramètres et les empreintes du Parquet et du manifeste sources. Les fichiers manifest.json et schema.json permettent de contrôler les exports et leur structure.

## Formats et réutilisation

Le CSV UTF-8 utilise le point-virgule, les décimales un point ; importer les codes géographiques comme texte pour préserver les zéros et les codes corses. Le Parquet conserve les types et les valeurs manquantes. synthese.json contient les agrégats de lecture. Les exports ne contiennent ni identifiants d'entreprises ni coordonnées individuelles.

Mise à jour hebdomadaire avec le pipeline RGE, programmée le lundi à 7 h 23, heure de Paris ; l'heure effective dépend du lancement par GitHub Actions. Le code et les contrôles sont disponibles dans le [dépôt public](https://github.com/{settings.github_owner}/{settings.github_repo}). Le [tableau de lecture et la méthode]({page_url(settings, topic.slug)}) donnent accès aux téléchargements. Licence Ouverte 2.0 : citer ADEME, API Geo, INSEE et les transformations Legibat, avec la date d'observation. Aucune disponibilité, assurance décennale ou conformité de chantier n'est attestée par ces indicateurs.
"""


def publish_topics(topics, settings, http, state, org, base_publication):
    publications = {}
    for topic in topics:
        config = settings.model_copy(
            update={"datagouv_dataset_id": "", "methodology_url": page_url(settings, topic.slug)}
        )
        client = DataGouvClient(config, http, state)
        dataset = client.upsert_dataset(
            org,
            topic_description(topic, settings, base_publication),
            key=f"legibat-{topic.slug}-{settings.year}",
            title=topic.title,
            granularity=topic.granularity,
        )
        resources = client.upload_resources(dataset, topic.files)
        publications[topic.slug] = {
            "title": topic.title,
            "dataset_id": dataset["id"],
            "dataset_url": dataset["page"],
            "pages_url": page_url(settings, topic.slug),
            "resources": [
                {k: r[k] for k in ("id", "title", "url", "format", "type") if k in r}
                for r in resources
            ],
        }
    return publications


def render_topics(topics, root, settings, publication):
    files = []
    for topic in topics:
        info = publication.get("complementary", {}).get(topic.slug, {})
        folder = root / "publications" / topic.slug
        description = topic_description(topic, settings, publication)
        write_text(
            folder / "README.md",
            f"# {topic.title}\n\n{description}\n\n"
            + (
                f"[Publication data.gouv.fr]({info['dataset_url']})\n"
                if info
                else "Publication en préparation.\n"
            ),
        )
        package = {
            "profile": "tabular-data-package",
            "name": topic.slug,
            "licenses": [{"name": "etalab-2.0", "path": LICENSE_URL}],
            "resources": [
                {
                    "name": topic.slug,
                    "profile": "tabular-data-resource",
                    "path": topic.files[0].name,
                    "schema": topic.schema,
                    "dialect": {"delimiter": ";"},
                }
            ],
        }
        write_json(folder / "datapackage.json", package)
        metadata = {
            "@context": "https://schema.org",
            "@type": "Dataset",
            "name": topic.title,
            "description": topic.explanation,
            "url": page_url(settings, topic.slug),
            "dateModified": settings.as_of.isoformat(),
            "license": LICENSE_URL,
            "creator": {
                "@type": "Organization",
                "name": settings.organization_name,
                "url": settings.site_url,
            },
            "isBasedOn": publication.get("dataset_url", settings.methodology_url),
            "variableMeasured": [
                {"@type": "PropertyValue", "name": f["name"], "description": f["description"]}
                for f in topic.schema["fields"]
            ],
        }
        if info:
            metadata["sameAs"] = [info["dataset_url"]]
            metadata["distribution"] = [
                {"@type": "DataDownload", "contentUrl": r["url"], "encodingFormat": r["format"]}
                for r in info["resources"]
                if r["format"] in {"csv", "parquet"}
            ]
        write_json(folder / "dataset.jsonld", metadata)
        downloads = "".join(
            f'<li><a href="{html.escape(r["url"], quote=True)}">{html.escape(r["title"])}</a></li>'
            for r in info.get("resources", [])
            if r["format"] != "html"
        )
        if topic.slug == ABSENCE:
            summary = topic.summary
            metrics = [
                (summary["nb_communes_sans_etablissement_rge"], "communes sélectionnées"),
                (summary["nb_communes_reference"], "communes dans le référentiel"),
                (
                    f"{summary['part_communes_sans_etablissement_pct']:.1f} %",
                    "part des communes du référentiel",
                ),
            ]
            table_rows = sorted(
                summary["departements"], key=lambda d: -d["nb_communes_sans_etablissement_rge"]
            )[:15]
            headers = [
                "Département",
                "Communes sélectionnées",
                "Communes du référentiel",
                "Part (%)",
            ]
            values = [
                [
                    d["code_departement"],
                    d["nb_communes_sans_etablissement_rge"],
                    d["nb_communes_reference"],
                    round(d["part_communes_sans_etablissement_pct"], 1),
                ]
                for d in table_rows
            ]
            caption = "15 départements ou territoires ayant le plus de communes sélectionnées. Les effectifs dépendent du nombre de communes : ce tableau n'est pas un classement de pénurie."
        else:
            summary = topic.summary
            metrics = [
                (summary["nb_departements_ou_territoires"], "départements et territoires"),
                (summary["nb_familles"], "familles de travaux non exclusives"),
                (summary["nb_lignes"], "lignes département × famille"),
            ]
            headers = ["Famille", "Qualifications", "Communes avec qualification"]
            values = [
                [r["famille_travaux"], r["nb_qualifications"], r["nb_communes_avec_qualification"]]
                for r in summary["familles"]
            ]
            caption = "Totaux par famille. Une même qualification peut relever de plusieurs familles : ne pas additionner ces lignes."
        cards = "".join(
            f'<div class="metric"><strong>{format_fr(v) if isinstance(v, int) else html.escape(v)}</strong><span>{html.escape(label)}</span></div>'
            for v, label in metrics
        )
        table = (
            '<div class="table-scroll"><table><caption>'
            + html.escape(caption)
            + "</caption><thead><tr>"
            + "".join('<th scope="col">' + html.escape(x) + "</th>" for x in headers)
            + "</tr></thead><tbody>"
            + "".join(
                "<tr>" + "".join("<td>" + html.escape(str(x)) + "</td>" for x in row) + "</tr>"
                for row in values
            )
            + "</tbody></table></div>"
        )
        dataset_link = (
            f'<a href="{html.escape(info["dataset_url"], quote=True)}">Jeu de données data.gouv.fr</a>'
            if info
            else "<span>Publication en préparation</span>"
        )
        body = f'''<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(topic.title)} — Legibat</title><meta name="description" content="{html.escape(topic.explanation[:200], quote=True)}"><link rel="canonical" href="{page_url(settings, topic.slug)}"><style>{STYLE}</style><script type="application/ld+json">{safe_jsonld(metadata)}</script></head><body><main><nav><a href="../">Cartographie RGE</a>{dataset_link}<a href="../publications/">Toutes les publications</a></nav><p class="eyebrow">Legibat · Données ouvertes · {settings.year}</p><h1>{html.escape(topic.title)}</h1><p>Observation au {settings.as_of.isoformat()} · Actualisation hebdomadaire</p><p class="note">Ces indicateurs décrivent une implantation et des qualifications recensées. Ils ne mesurent ni les disponibilités, ni les délais, ni l'accès effectif aux travaux.</p><div class="metrics">{cards}</div><h2>Lire les résultats</h2><p>{html.escape(topic.explanation)}</p>{table}<h2>Télécharger les données</h2><ul>{downloads}</ul><h2>Sources et méthode</h2><p>Collecte complète ADEME et référentiel API Geo. Logements INSEE {settings.housing_year}, géographie {settings.housing_geo_year}. Les valeurs absentes restent manquantes ; les correspondances ambiguës sont exclues.</p><p><a href="https://github.com/{settings.github_owner}/{settings.github_repo}/tree/{settings.github_branch}/publications/{topic.slug}">Méthode complète, dictionnaire et code reproductible</a> · <a href="{settings.methodology_url}">Méthode de la cartographie source</a></p><footer><p>Licence Ouverte 2.0 · Citer ADEME, API Geo, INSEE et les transformations Legibat. <a href="{settings.site_url}">Legibat</a>.</p></footer></main></body></html>'''
        page = root / "docs" / topic.slug / "index.html"
        write_text(page, body)
        files += (
            topic.files
            + [folder / name for name in ("README.md", "datapackage.json", "dataset.jsonld")]
            + [page]
        )
    links = "".join(
        f'<article class="metric"><h2><a href="../{t.slug}/">{html.escape(t.title)}</a></h2><p>{html.escape(t.explanation[:260])}…</p></article>'
        for t in topics
    )
    catalog = root / "docs/publications/index.html"
    write_text(
        catalog,
        f'<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Publications Open Data RGE — Legibat</title><style>{STYLE}</style></head><body><main><nav><a href="../">Cartographie RGE</a></nav><p class="eyebrow">Legibat · Observatoire RGE</p><h1>Publications complémentaires</h1><p>Observation au {settings.as_of.isoformat()} · Sources et calculs ouverts, actualisés chaque semaine.</p>{links}</main></body></html>',
    )
    files.append(catalog)
    return files
