"""Documentation, dictionnaire, paquet Frictionless et citation générés depuis le calcul."""

import html
from pathlib import Path

import yaml

from common import sha256, write_json, write_text

LICENSE_URL = "https://www.etalab.gouv.fr/licence-ouverte-open-licence/"
INSEE_PAGE = "https://www.insee.fr/fr/statistiques/2521169"


def format_fr(value, decimals=0):
    return f"{value:,.{decimals}f}".replace(",", "\u202f").replace(".", ",")


def description(result, settings):
    s, q = settings, result.quality["counts"]
    table = "\n".join(
        f"| `{f['name']}` | {f['type']} | {f['description']} |" for f in result.schema["fields"]
    )
    warning = (
        "**DÉMONSTRATION / ÉCHANTILLON : chiffres non représentatifs, publication interdite.**\n\n"
        if result.provenance["partial"]
        else ""
    )
    housing = (
        f"Les logements proviennent du recensement INSEE {s.housing_year}, géographie {s.housing_geo_year}. "
        "Le filtre retient uniquement les communes, la mesure DWELLINGS et l'exploitation principale du logement. "
        "Les résidences principales, secondaires et logements vacants appartiennent au parc total. "
        "Les valeurs pondérées du recensement sont conservées avec leurs décimales, sans arrondi artificiel en entier."
        if s.housing_mode == "insee"
        else f"Le dénominateur est une estimation explicite : population divisée par {s.household_size}. "
        "Ce proxy approxime un nombre de ménages et ne représente pas le parc total de logements. "
        "Il ignore notamment les résidences secondaires, la vacance et les personnes vivant en collectivité. "
        "Il ne doit jamais être présenté comme un dénombrement INSEE des logements."
    )
    return f"""{warning}## Objet et intérêt public

Cette édition {s.year}, observée au {s.as_of.isoformat()}, rapproche la localisation des professionnels de travaux RGE avec la population et le parc de logements des communes françaises. Elle propose un indicateur descriptif de présence locale pour faciliter l'exploration des territoires, les diagnostics préalables et les réutilisations citoyennes. Legibat réalise ce rapprochement en tant que producteur privé. L'ADEME, l'INSEE et les administrations fournissent les sources ; elles ne valident ni les seuils choisis ni les conclusions de cette étude dérivée.

La rénovation énergétique mobilise plusieurs métiers. Une présence locale faible peut justifier une analyse complémentaire des entreprises voisines, des transports et de l'organisation des chantiers. Le jeu contribue à informer les ménages et les acteurs territoriaux dans un contexte de prévention des fraudes aux travaux. Il ne permet cependant pas de qualifier une entreprise de frauduleuse, de contrôler sa garantie décennale, de confirmer une attestation Consuel, ni d'estimer un délai d'intervention. La mention RGE ne remplace pas les vérifications documentaires adaptées à chaque chantier.

## Sources, périmètre et date d'observation

La liste des entreprises RGE est collectée auprès de [l'ADEME]({s.ademe_dataset_url}). La collecte parcourt les pages jusqu'au total annoncé, avec un ordre stable et un contrôle des identifiants. Une modification détectée de la source pendant le téléchargement arrête l'exécution. Les communes, codes postaux, départements, régions et populations sont issus de [l'API Découpage administratif]({s.geo_api_url}). Cette API ne fournit pas le millésime de population dans la réponse utilisée : la date de collecte est conservée et l'année de population n'est pas inventée.

{housing} La [base du comparateur de territoires de l'INSEE]({INSEE_PAGE}) décrit sa géographie et ses limites. Une commune absente du fichier logement conserve une valeur manquante. Les changements de communes peuvent créer des écarts entre référentiels ; aucun transfert historique implicite n'est appliqué. Le périmètre final est celui des communes renvoyées par l'API Geo, sans prétendre couvrir les collectivités absentes de ce référentiel.

## Méthode de calcul

Une qualification est retenue si sa date de début est inférieure ou égale à la date d'observation et sa date de fin supérieure ou égale à cette date. Les lignes sans dates exploitables ou sans SIRET à quatorze chiffres sont écartées et comptées dans le rapport de qualité. La liste courante de l'ADEME n'est pas une archive exhaustive : appliquer une date ancienne ne reconstitue pas automatiquement la situation de cette année. Le calcul porte sur les activités de travaux ; les études, audits et architectes sont exclus de ce périmètre.

Le rattachement utilise le code commune lorsqu'il existe. À défaut, le code postal est croisé avec le nom normalisé de commune. Un code postal correspondant à une seule commune peut être utilisé seul. Si plusieurs communes restent possibles, la ligne est exclue du calcul communal. Aucune multiplication d'une entreprise dans toutes les communes d'un même code postal n'est pratiquée. Paris, Lyon et Marseille sont regroupées à la commune entière lorsqu'un code d'arrondissement municipal est rencontré. Un établissement rattaché à plusieurs communes est exclu de tous les agrégats. La publication est bloquée si la proportion de ces exclusions dépasse le seuil de qualité configuré ({100 * s.max_conflicting_fraction:g} % des établissements localisés).

Les établissements sont dédupliqués par SIRET ; les entreprises par SIREN. Une entreprise peut disposer de plusieurs établissements. Le nombre de qualifications est calculé sur les combinaisons distinctes établissement, organisme, code de qualification et domaine normalisé. Les familles photovoltaïque, pompe à chaleur, isolation, chauffage et menuiserie peuvent se chevaucher : une pompe à chaleur destinée au chauffage relève de deux familles. Les compteurs par famille ne doivent donc pas être additionnés pour obtenir un nombre d'entreprises. Les domaines de travaux non classés restent visibles dans le rapport de qualité.

Les ratios communaux valent mille fois le nombre d'entreprises RGE divisé par la population ou les logements. Un dénominateur nul ou absent produit une valeur manquante. Le classement utilise le ratio logements avant arrondi : zéro entreprise donne « Désert RGE » si les logements sont positifs ; un ratio positif inférieur à {s.tension_low:g} donne « Sous-capacité » ; un ratio compris entre {s.tension_low:g} inclus et {s.tension_high:g} exclu donne « Équilibré » ; à partir de {s.tension_high:g}, la classe est « Forte couverture ». Sans dénominateur exploitable, la classe est « Non calculable ».

Ces appellations correspondent à des seuils éditoriaux configurables, sans validation comme mesure officielle de pénurie ou de capacité. Une entreprise implantée ailleurs peut intervenir dans la commune. Les effectifs salariés, carnets de commandes, spécialités disponibles, rayons d'intervention et besoins réels en rénovation ne sont pas mesurés. L'absence d'établissement local n'est donc pas la preuve d'une absence de service. Les petites communes sont particulièrement sensibles aux variations d'un seul établissement.

## Contrôles, résultats et réutilisation

L'exécution contient {format_fr(q["communes"])} communes, {format_fr(q["matched_establishments"])} établissements et {format_fr(q["matched_companies"])} entreprises distinctes rattachées, après filtrage. Le rapport précise les lignes rejetées, les correspondances ambiguës et les logements manquants. Les agrégats départementaux et régionaux recomptent les entreprises distinctes à chaque niveau. Ils ne sont pas la simple somme des entreprises communales. Pour chaque ratio agrégé, le numérateur est limité aux communes ayant le dénominateur correspondant, afin de conserver un périmètre comparable.

Les CSV UTF-8 sont disponibles avec séparateur point-virgule et virgule ; les nombres utilisent un point décimal. Les codes géographiques doivent être importés comme texte, notamment pour conserver les zéros initiaux et les codes corses. Le Parquet préserve les types et valeurs nulles. Les fichiers JSON apportent les synthèses, la provenance et les contrôles. Le TableSchema décrit les colonnes et les contraintes, et le manifeste donne les empreintes SHA-256 des exports. Les données individuelles, coordonnées de contact et SIRET ne figurent pas dans les exports publics.

La mise à jour est programmée chaque lundi à 7 h 23, heure de Paris, via GitHub Actions. Chaque exécution collecte les sources, contrôle la qualité, actualise les publications et vérifie leur disponibilité publique. Les fichiers sont diffusés sous Licence Ouverte 2.0 avec attribution des sources et indication des transformations. Le schéma et les métadonnées facilitent la découverte et l'interopérabilité ; ils ne garantissent pas un score de qualité, un référencement, un moissonnage par un portail tiers ou un délai d'indexation. Les choix de collecte appartiennent à chaque portail.

La [méthodologie complète et les limites de l'étude]({s.methodology_url}) sont préparées pour Legibat. Le [service Legibat consacré aux vérifications de conformité]({s.site_url}) constitue un lien contextuel distinct des sources officielles. Les URL locales du fichier sont construites selon la convention de routage fournie par Legibat ; leur présence ne prouve pas que chaque page existe ou qu'une entreprise a été vérifiée. Toute réutilisation doit conserver cette distinction.

## Dictionnaire des variables

| Variable | Type | Définition |
|---|---|---|
{table}
"""


def regional_svg(regions):
    width, row_height = 960, 34
    height = 100 + row_height * len(regions)
    rates = [r["ratio_artisans_1000_logements"] or 0 for r in regions]
    maximum = max(rates, default=1) or 1
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        '<title id="title">Entreprises RGE pour 1 000 logements, par région</title>',
        '<desc id="desc">Ratios calculés sur les communes au dénominateur connu. Le graphique ne mesure pas les disponibilités des entreprises.</desc>',
        f'<rect width="{width}" height="{height}" fill="#f7f8f5"/>',
        '<g font-family="Arial, sans-serif" fill="#17332e"><text x="24" y="34" font-size="20" font-weight="bold">Entreprises RGE / 1 000 logements</text>',
    ]
    for i, r in enumerate(regions):
        y = 72 + i * row_height
        rate = r["ratio_artisans_1000_logements"]
        label = format_fr(rate, 2) if rate is not None else "ND"
        bar = 480 * (rate or 0) / maximum
        lines.append(
            f'<text x="24" y="{y + 16}" font-size="14">{html.escape(r["nom_region"])}</text><rect x="300" y="{y}" width="{bar:.2f}" height="22" rx="3" fill="#267665"/><text x="{max(310, 310 + bar):.2f}" y="{y + 16}" font-size="14">{label}</text>'
        )
    return "\n".join(lines) + "</g></svg>\n"


def jsonld(result, settings, publication):
    is_sample = result.provenance["partial"]
    name = ("Démonstration — " if is_sample else "") + settings.title
    data = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": name,
        "@id": "urn:legibat:rge:demonstration"
        if is_sample
        else settings.methodology_url + "#dataset",
        "description": "Présence locale des entreprises de travaux RGE, rapprochée de la population et du parc de logements. Indicateurs descriptifs avec contrôle des qualifications, sources, données manquantes et limites géographiques documentées. "
        + (
            "Données fictives ou partielles, non représentatives."
            if is_sample
            else "Seuils éditoriaux ; aucune mesure des disponibilités ou des délais d'intervention."
        ),
        "creator": {
            "@type": "Organization",
            "name": settings.organization_name,
            "url": settings.site_url,
        },
        "license": LICENSE_URL,
        "inLanguage": "fr",
        "isAccessibleForFree": True,
        "dateModified": settings.as_of.isoformat(),
        "temporalCoverage": settings.as_of.isoformat(),
        "spatialCoverage": {
            "@type": "Place",
            "name": "France — périmètre communal disponible dans l'API Geo",
        },
        "variableMeasured": [
            {"@type": "PropertyValue", "name": f["name"], "description": f["description"]}
            for f in result.schema["fields"]
        ],
        "keywords": ["RGE", "rénovation énergétique", "logement", "couverture communale"],
    }
    if not is_sample:
        data["isBasedOn"] = [settings.ademe_dataset_url, settings.geo_api_url + "/communes"]
        if settings.housing_mode == "insee":
            data["isBasedOn"].append(INSEE_PAGE)
    if publication.get("dataset_url"):
        data["url"] = settings.methodology_url
        data["sameAs"] = [
            u
            for u in [
                publication.get("dataset_url"),
                publication.get("github_url"),
                publication.get("pages_url"),
                settings.methodology_url,
            ]
            if u
        ]
        data["includedInDataCatalog"] = {
            "@type": "DataCatalog",
            "name": "data.gouv.fr",
            "url": "https://www.data.gouv.fr/",
        }
        data["distribution"] = [
            {
                "@type": "DataDownload",
                "name": r["title"],
                "encodingFormat": {
                    "csv": "text/csv",
                    "parquet": "application/vnd.apache.parquet",
                    "json": "application/json",
                }.get(r["format"], r["format"]),
                "contentUrl": r.get("latest") or r["url"],
            }
            for r in publication.get("resources", [])
            if r.get("type") == "main"
        ]
    return data


def generate_metadata(result, output: Path, settings, files, publication):
    text = description(result, settings)
    write_text(output / "DATASET_DESCRIPTION.md", text)
    rows = []
    for r in result.regions:
        rate = r["ratio_artisans_1000_logements"]
        rate_label = format_fr(rate, 3) if rate is not None else "ND"
        rows.append(
            f"| {r['nom_region']} | {r['nb_communes']} | {r['nb_entreprises_rge']} | {r['nb_etablissements_rge']} | {rate_label} |"
        )
    public_link = (
        f"[Jeu de données officiel sur data.gouv.fr]({publication['dataset_url']})"
        if publication.get("dataset_url")
        else "Aucune publication data.gouv.fr effectuée par cette exécution."
    )
    write_text(
        output / "README.md",
        f"""# {settings.title}

{"**DÉMONSTRATION : données fictives ou partielles, sans portée statistique.**" if result.provenance["partial"] else "**Données agrégées issues des sources officielles ; indicateur éditorial Legibat.**"}

{public_link}

[Guide de lancement](GUIDE.md) · [Méthodologie](DATASET_DESCRIPTION.md) · [Sources et API](SOURCES.md) · [Étude Legibat]({settings.methodology_url})

## Résultats par région

| Région | Communes | Entreprises distinctes | Établissements | Entreprises / 1 000 logements |
|---|---:|---:|---:|---:|
{chr(10).join(rows)}

Les ratios portent sur les communes au dénominateur connu. Les SIREN sont dédupliqués à chaque niveau géographique ; les nombres d'entreprises des communes ne s'additionnent pas directement.

![Entreprises RGE par région](docs/assets/couverture-regions.svg)

## Fichiers

- [CSV, séparateur point-virgule](dist_data/{settings.stem}.csv)
- [CSV, séparateur virgule](dist_data/{settings.stem}-comma.csv)
- [Parquet typé](dist_data/{settings.stem}.parquet)
- [Synthèse départementale](dist_data/synthese-departementale-rge-{settings.year}.json)
- [TableSchema](dist_data/schema.json), [qualité](dist_data/quality-report.json), [provenance](dist_data/provenance.json), [empreintes](dist_data/manifest.json)
- [Data Package](datapackage.json) et [citation](CITATION.cff)

## Exécution

```bash
python -m venv .venv
python -m pip install -r requirements.txt
python main.py --dry-run --source sample
python main.py --dry-run --source live
```

Activer l'environnement avant l'installation, comme décrit dans le guide. La commande `--publish --source live` exige les clés et identifiants de vos comptes. Les sorties de démonstration ne peuvent pas être publiées par le pipeline.

{text}
""",
    )
    write_text(output / "docs/assets/couverture-regions.svg", regional_svg(result.regions))
    citation = {
        "cff-version": "1.2.0",
        "message": "Citer cette étude en conservant les sources ADEME, API Geo et INSEE et les limites méthodologiques.",
        "type": "dataset",
        "title": ("Démonstration — " if result.provenance["partial"] else "") + settings.title,
        "authors": [{"name": settings.organization_name}],
        "version": settings.as_of.isoformat(),
        "license": "etalab-2.0",
        "url": settings.methodology_url,
    }
    if publication.get("dataset_url"):
        citation["date-released"] = settings.as_of.isoformat()
    if publication.get("github_url"):
        citation["repository-code"] = publication["github_url"]
    write_text(
        output / "CITATION.cff", yaml.safe_dump(citation, allow_unicode=True, sort_keys=False)
    )
    resources = []
    for path in files:
        if path.suffix not in {".csv", ".parquet"}:
            continue
        resource = {
            "name": path.stem + "-" + path.suffix[1:],
            "path": path.relative_to(output).as_posix(),
            "format": path.suffix[1:],
            "bytes": path.stat().st_size,
            "hash": "sha256:" + sha256(path),
        }
        if path.suffix == ".csv":
            resource.update(
                profile="tabular-data-resource",
                encoding="utf-8",
                schema=result.schema,
                dialect={"delimiter": "," if path.stem.endswith("-comma") else ";"},
            )
        resources.append(resource)
    write_json(
        output / "datapackage.json",
        {
            "profile": "data-package",
            "name": f"legibat-rge-communes-{settings.year}",
            "title": citation["title"],
            "description": jsonld(result, settings, publication)["description"],
            "licenses": [
                {"name": "etalab-2.0", "path": LICENSE_URL, "title": "Licence Ouverte 2.0"}
            ],
            "contributors": [
                {
                    "title": settings.organization_name,
                    "role": "publisher",
                    "path": settings.site_url,
                }
            ],
            "sources": [
                {"title": "ADEME — entreprises RGE", "path": settings.ademe_dataset_url},
                {"title": "API Geo", "path": settings.geo_api_url},
                {"title": "INSEE", "path": INSEE_PAGE},
            ],
            "resources": resources,
        },
    )
    write_json(output / "dataset.jsonld", jsonld(result, settings, publication))
    return [
        output / p
        for p in [
            "README.md",
            "DATASET_DESCRIPTION.md",
            "CITATION.cff",
            "datapackage.json",
            "dataset.jsonld",
            "docs/assets/couverture-regions.svg",
        ]
    ]
