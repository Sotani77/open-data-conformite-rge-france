"""Collecte officielle, rattachement conservateur et agrégats communaux reproductibles."""

import logging
import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pandas as pd
import pyarrow.parquet as pq

from common import Http, PipelineError, sha256, write_json
from config import Settings

LOG = logging.getLogger(__name__)
FAMILIES = ("pv", "pac", "isolation", "chauffage", "menuiserie")
LABELS = [
    "Désert RGE",
    "Sous-capacité",
    "Équilibré",
    "Forte couverture",
    "Non calculable",
    "Échantillon",
]
SELECT = [
    "siret",
    "code_postal",
    "commune",
    "code_qualification",
    "domaine",
    "meta_domaine",
    "organisme",
    "lien_date_debut",
    "lien_date_fin",
    "_id",
    "_i",
]


def normalize(value):
    text = unicodedata.normalize("NFKD", str(value or "").lower().replace("œ", "oe"))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"\bste\b", "sainte", re.sub(r"\bst\b", "saint", text))
    return " ".join(re.findall(r"[a-z0-9]+", text))


def slug(value):
    return normalize(value).replace(" ", "-")


def commune_parent(code):
    code = str(code).strip().upper().zfill(5)
    if re.fullmatch(r"751(0[1-9]|1[0-9]|20)", code):
        return "75056"
    if re.fullmatch(r"6938[1-9]", code):
        return "69123"
    if re.fullmatch(r"132(0[1-9]|1[0-6])", code):
        return "13055"
    return code


class Geography:
    def __init__(self, communes):
        self.by_code = {c["code"]: c for c in communes}
        if len(self.by_code) != len(communes) or not communes:
            raise PipelineError("Référentiel communal vide ou codes dupliqués")
        self.by_postal = defaultdict(set)
        for c in communes:
            for cp in c.get("codesPostaux", []):
                self.by_postal[str(cp).zfill(5)].add(c["code"])

    def match(self, row):
        direct = row.get("code_insee") or row.get("code_commune")
        if direct:
            code = commune_parent(direct)
            if code in self.by_code:
                return code, "insee"
            return None, "insee_inconnu"
        cp = str(row.get("code_postal") or "").strip().zfill(5)
        candidates = self.by_postal.get(cp, set())
        name = normalize(row.get("commune"))
        exact = [c for c in candidates if normalize(self.by_code[c]["nom"]) == name]
        if len(exact) == 1:
            return exact[0], "postal_nom"
        if len(candidates) == 1:
            return next(iter(candidates)), "postal_unique"
        return None, "postal_ambigu" if candidates else "postal_inconnu"


def domain_families(domain):
    n = normalize(domain)
    if n.startswith("etude") or any(x in n for x in ["audit", "architecte"]):
        return set()
    result = set()
    if "photovoltai" in n:
        result.add("pv")
    if "pompe a chaleur" in n or "thermodynamique" in n:
        result.add("pac")
    if "isolation" in n:
        result.add("isolation")
    if any(x in n for x in ["chauffage", "chaudiere", "poele", "insert bois", "radiateurs"]):
        result.add("chauffage")
    if any(x in n for x in ["fenetre", "volet", "menuiserie", "portes donnant"]):
        result.add("menuiserie")
    return result


def parse_date(value):
    if not isinstance(value, str) or not value.strip():
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value[:10], fmt).date()
        except ValueError:
            continue
    return None


def tension(n, housing, settings, partial=False):
    if partial:
        return "Échantillon"
    if housing is None or not math.isfinite(housing) or housing <= 0:
        return "Non calculable"
    if n == 0:
        return "Désert RGE"
    rate = n * 1000 / housing
    if rate < settings.tension_low:
        return "Sous-capacité"
    if rate < settings.tension_high:
        return "Équilibré"
    return "Forte couverture"


def ratio(n, denominator):
    return round(n * 1000 / denominator, 6) if denominator is not None and denominator > 0 else None


def load_housing(path: Path, settings: Settings):
    """Parquet INSEE 2026 en format long, ou table normalisée CSV/Parquet.

    Les lignes ARM, BV2022, totaux nationaux et millésimes différents sont exclus.
    Une valeur manquante n'est jamais convertie en zéro.
    """
    if path.suffix == ".parquet":
        names = pq.ParquetFile(path).schema_arrow.names
        if "TAB_MEASURE" in names:
            filters = [
                ("GEO_OBJECT", "=", "COM"),
                ("TAB_MEASURE", "=", "DWELLINGS"),
                ("DS", "=", "DS_RP_LOGEMENT_PRINC"),
                ("TIME_PERIOD", "=", str(settings.housing_year)),
                ("GEO_REF", "=", str(settings.housing_geo_year)),
            ]
            df = pd.read_parquet(
                path, filters=filters, columns=["GEO", "OBS_VALUE", "UNIT_MULT", "CONF_STATUS"]
            )
            if df.empty:
                raise PipelineError("Aucun logement INSEE pour le millésime/géographie configuré")
            df["nb_logements"] = pd.to_numeric(df["OBS_VALUE"], errors="coerce") * (
                10 ** pd.to_numeric(df["UNIT_MULT"], errors="coerce").fillna(0)
            )
            # Les données confidentielles sont absentes, même si une valeur existe.
            df.loc[df["CONF_STATUS"].fillna("").isin(["C", "S"]), "nb_logements"] = float("nan")
            df = df.rename(columns={"GEO": "code_insee"})[["code_insee", "nb_logements"]]
        else:
            df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path, sep=None, engine="python", dtype={"code_insee": "string"})
    if not {"code_insee", "nb_logements"}.issubset(df.columns):
        raise PipelineError("Table logements : colonnes code_insee et nb_logements requises")
    df["code_insee"] = df["code_insee"].astype(str).str.strip().str.upper().str.zfill(5)
    if df["code_insee"].duplicated().any():
        raise PipelineError(
            "Plusieurs observations logements pour une commune ; filtrer le millésime"
        )
    values = pd.to_numeric(df["nb_logements"], errors="raise")
    if ((values < 0) | (values == float("inf"))).any():
        raise PipelineError("Nombre de logements négatif ou infini")
    return {c: (None if pd.isna(v) else float(v)) for c, v in zip(df["code_insee"], values)}


def fetch_live(settings, cache: Path, http: Http, limit=None):
    cache.mkdir(parents=True, exist_ok=True)
    metadata = http.json("GET", settings.ademe_dataset_url)
    available = {f["key"] for f in metadata["schema"]}
    required = set(SELECT) - {"_i", "_id"}
    if not required.issubset(available):
        raise PipelineError("Schéma ADEME modifié : " + ", ".join(sorted(required - available)))
    selected = [f for f in SELECT if f in available]
    selected += [f for f in ("code_insee", "code_commune") if f in available]
    params = {"size": settings.page_size, "select": ",".join(selected), "sort": "_i"}
    url = settings.ademe_dataset_url.rstrip("/") + "/lines"
    rows, visited, ids = [], set(), set()
    expected = None
    while url:
        parsed, origin = urlsplit(url), urlsplit(settings.ademe_dataset_url)
        if (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc):
            raise PipelineError("Lien de pagination ADEME hors origine")
        if url in visited:
            raise PipelineError("Boucle de pagination ADEME")
        visited.add(url)
        page = http.json("GET", url, params=params)
        params = None
        if expected is None:
            expected = int(page["total"])
        elif "total" in page and int(page["total"]) != expected:
            raise PipelineError("La source ADEME a changé pendant la pagination ; relancer")
        batch = page.get("results", [])
        if not batch and len(rows) < expected:
            raise PipelineError("Pagination ADEME interrompue")
        for row in batch:
            row_id = row.get("_id", row.get("_i"))
            if row_id is None or row_id in ids:
                raise PipelineError("Identifiant de ligne ADEME absent ou dupliqué")
            ids.add(row_id)
        rows.extend(batch)
        if len(visited) % 20 == 0:
            LOG.info("ADEME : %s / %s lignes", len(rows), expected)
        if limit and len(rows) >= limit:
            rows = rows[:limit]
            break
        url = page.get("next")
    if not limit and len(rows) != expected:
        raise PipelineError(f"Collecte ADEME incomplète : {len(rows)} / {expected}")
    after = http.json("GET", settings.ademe_dataset_url)
    if metadata.get("dataUpdatedAt") != after.get("dataUpdatedAt"):
        raise PipelineError("Actualisation ADEME pendant la collecte ; relancer")
    communes = http.json(
        "GET",
        settings.geo_api_url.rstrip("/") + "/communes",
        params={"fields": "nom,code,codesPostaux,codeDepartement,codeRegion,population"},
    )
    regions = http.json("GET", settings.geo_api_url.rstrip("/") + "/regions")
    for name, data in [("rge.json", rows), ("communes.json", communes), ("regions.json", regions)]:
        write_json(cache / name, data)
    source = {
        "mode": "live",
        "partial": bool(limit),
        "ademe_url": settings.ademe_dataset_url,
        "ademe_data_updated_at": metadata.get("dataUpdatedAt"),
        "ademe_total": expected,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "geo_url": settings.geo_api_url + "/communes",
        "population_millesime": "non fourni par l'API Geo ; date de collecte conservée",
        "ademe_license": metadata.get("license"),
        "input_sha256": {
            n: sha256(cache / n) for n in ["rge.json", "communes.json", "regions.json"]
        },
    }
    housing = {}
    if settings.housing_mode == "insee":
        path = settings.housing_file
        if path is None:
            path = cache / "comparateur.parquet"
            response = http.request("GET", settings.insee_url)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(response.content)
            tmp.replace(path)
        housing = load_housing(path, settings)
        source["housing"] = {
            "url": settings.insee_url if settings.housing_file is None else None,
            "file": path.name,
            "sha256": sha256(path),
            "year": settings.housing_year,
            "geography_year": settings.housing_geo_year,
        }
    else:
        source["housing"] = {
            "method": "population / household_size",
            "household_size": settings.household_size,
        }
    return rows, communes, regions, housing, source


def sample_inputs(settings):
    """Petit exemple entièrement fictif, sans réseau et interdit à la publication."""
    communes = [
        {
            "code": "01001",
            "nom": "L'Abergement-Clémenciat",
            "codesPostaux": ["01400"],
            "codeDepartement": "01",
            "codeRegion": "84",
            "population": 800,
        },
        {
            "code": "01002",
            "nom": "L'Abergement-de-Varey",
            "codesPostaux": ["01640"],
            "codeDepartement": "01",
            "codeRegion": "84",
            "population": 300,
        },
        {
            "code": "75056",
            "nom": "Paris",
            "codesPostaux": ["75001"],
            "codeDepartement": "75",
            "codeRegion": "11",
            "population": 2000000,
        },
        {
            "code": "2A004",
            "nom": "Ajaccio",
            "codesPostaux": ["20000"],
            "codeDepartement": "2A",
            "codeRegion": "94",
            "population": 70000,
        },
    ]
    rows = []
    for i, (code, domain) in enumerate(
        [
            ("01001", "Pompe à chaleur : chauffage"),
            ("01001", "Isolation des combles perdus"),
            ("75056", "Panneaux solaires photovoltaïques"),
            ("2A004", "Fenêtres de toit"),
        ]
    ):
        c = next(c for c in communes if c["code"] == code)
        rows.append(
            {
                "siret": f"00000000{i}00001",
                "commune": c["nom"],
                "code_postal": c["codesPostaux"][0],
                "domaine": domain,
                "meta_domaine": "Travaux d'efficacité énergétique",
                "organisme": "exemple",
                "code_qualification": f"EX{i}",
                "lien_date_debut": "2000-01-01",
                "lien_date_fin": "2099-12-31",
            }
        )
    regions = [
        {"code": "84", "nom": "Auvergne-Rhône-Alpes"},
        {"code": "11", "nom": "Île-de-France"},
        {"code": "94", "nom": "Corse"},
    ]
    return (
        rows,
        communes,
        regions,
        {"01001": 400, "01002": 150, "75056": 1200000, "2A004": 40000},
        {
            "mode": "sample",
            "partial": True,
            "collected_at": settings.as_of.isoformat(),
            "housing": {"method": "valeurs fictives"},
            "ademe_total": len(rows),
        },
    )


@dataclass
class BuildResult:
    frame: pd.DataFrame
    departments: list
    regions: list
    quality: dict
    provenance: dict
    schema: dict


def build_dataset(rows, communes, regions, housing, provenance, settings):
    geo = Geography(communes)
    stats = Counter(raw_rows=len(rows))
    valid = []
    unmatched_domains = Counter()
    for row in rows:
        begin, end = parse_date(row.get("lien_date_debut")), parse_date(row.get("lien_date_fin"))
        if begin is None or end is None or end < begin:
            stats["invalid_dates"] += 1
            continue
        if not begin <= settings.as_of <= end:
            stats["inactive_rows"] += 1
            continue
        n = normalize(row.get("meta_domaine"))
        domain = str(row.get("domaine") or "")
        parts = [p.strip() for p in domain.split(";") if p.strip()]
        work_domains = [
            p
            for p in parts
            if not normalize(p).startswith("etude")
            and not any(
                x in normalize(p) for x in ["architecte", "audit", "inconnu", "non renseigne"]
            )
        ]
        if "etudes" in n or not work_domains:
            stats["outside_work_scope"] += 1
            continue
        siret = str(row.get("siret") or "").strip()
        if not re.fullmatch(r"\d{14}", siret):
            stats["invalid_siret"] += 1
            continue
        stats["eligible_rows"] += 1
        code, method = geo.match(row)
        stats["matching_" + method] += 1
        if code is None:
            stats["unmatched_rows"] += 1
            continue
        for part in work_domains:
            families = domain_families(part)
            if not families:
                unmatched_domains[part] += 1
            valid.append(
                {
                    "code": code,
                    "siret": siret,
                    "siren": siret[:9],
                    "qualification": (
                        siret,
                        str(row.get("organisme") or ""),
                        str(row.get("code_qualification") or ""),
                        normalize(part),
                    ),
                    "families": families,
                }
            )
    # Un même établissement localisé dans plusieurs communes est exclu de toutes :
    # aucune décision arbitraire de répartition ou de déplacement historique.
    locations = defaultdict(set)
    for r in valid:
        locations[r["siret"]].add(r["code"])
    conflicts = {k for k, v in locations.items() if len(v) > 1}
    valid = [r for r in valid if r["siret"] not in conflicts]
    stats["conflicting_establishments"] = len(conflicts)
    by_commune = defaultdict(list)
    for r in valid:
        by_commune[r["code"]].append(r)
    names = {r["code"]: r["nom"] for r in regions}
    records = []
    partial = provenance.get("partial", False)
    for c in sorted(communes, key=lambda c: c["code"]):
        code = c["code"]
        members = by_commune[code]
        companies = len({r["siren"] for r in members})
        pop = c.get("population")
        pop = int(pop) if pop is not None and pop >= 0 else None
        nh = housing.get(code)
        housing_source = "INSEE" if nh is not None else "manquant"
        if provenance["mode"] == "sample":
            housing_source = "fictif"
        elif settings.housing_mode == "estimate":
            nh = round(pop / settings.household_size, 6) if pop is not None else None
            housing_source = "estimation_population" if nh is not None else "manquant"
        row = {
            "code_insee": code,
            "nom_commune": c["nom"],
            "code_departement": c.get("codeDepartement", ""),
            "code_region": c.get("codeRegion", ""),
            "nom_region": names.get(
                c.get("codeRegion"), f"Territoire {c.get('codeRegion', 'inconnu')}"
            ),
            "population": pop,
            "nb_logements": nh,
            "source_logements": housing_source,
            "millesime_logements": settings.housing_year if housing_source == "INSEE" else None,
            "nb_entreprises_rge": companies,
            "nb_etablissements_rge": len({r["siret"] for r in members}),
        }
        for family in FAMILIES:
            row["nb_qualif_" + family] = len(
                {r["qualification"] for r in members if family in r["families"]}
            )
        row.update(
            {
                "ratio_artisans_1000_hab": ratio(companies, pop),
                "ratio_artisans_1000_logements": ratio(companies, nh),
                "indice_tension_rge": tension(companies, nh, settings, partial),
                "url_verification_locale": f"{settings.site_url.rstrip('/')}/artisans-verifies/commune/{slug(c['nom'])}-{code}",
                "date_observation": settings.as_of.isoformat(),
                "donnees_partielles": partial,
            }
        )
        records.append(row)
    frame = pd.DataFrame(records)
    for col in [
        "population",
        "millesime_logements",
        "nb_entreprises_rge",
        "nb_etablissements_rge",
    ] + ["nb_qualif_" + f for f in FAMILIES]:
        frame[col] = frame[col].astype("Int64")
    for col in ["nb_logements", "ratio_artisans_1000_hab", "ratio_artisans_1000_logements"]:
        frame[col] = frame[col].astype("Float64")
    stats["matched_establishments"] = len({r["siret"] for r in valid})
    stats["matched_companies"] = len({r["siren"] for r in valid})
    stats["communes"] = len(frame)
    stats["communes_missing_housing"] = int(frame.nb_logements.isna().sum())
    stats["communes_estimated_housing"] = int(
        (frame.source_logements == "estimation_population").sum()
    )
    quality = {
        "counts": dict(stats),
        "unclassified_work_domains": dict(unmatched_domains.most_common()),
        "unmatched_fraction": stats["unmatched_rows"] / max(stats["eligible_rows"], 1),
        "invalid_fraction": (stats["invalid_dates"] + stats["invalid_siret"]) / max(len(rows), 1),
        "conflicting_fraction": len(conflicts) / max(len(locations), 1),
        "thresholds": {
            "unmatched_fraction": settings.max_unmatched_fraction,
            "invalid_fraction": settings.max_invalid_fraction,
            "conflicting_fraction": settings.max_conflicting_fraction,
        },
        "publishable": False,
        "blocking_reasons": [],
    }
    if partial:
        quality["blocking_reasons"].append("Données fictives ou collecte partielle")
    if not valid:
        quality["blocking_reasons"].append("Aucun établissement RGE valide")
    if quality["unmatched_fraction"] > settings.max_unmatched_fraction:
        quality["blocking_reasons"].append("Taux de rattachement géographique insuffisant")
    if quality["invalid_fraction"] > settings.max_invalid_fraction:
        quality["blocking_reasons"].append("Taux de dates/SIRET invalides excessif")
    if quality["conflicting_fraction"] > settings.max_conflicting_fraction:
        quality["blocking_reasons"].append(
            "Taux excessif d'établissements affectés à plusieurs communes"
        )
    if frame.nb_logements.isna().all():
        quality["blocking_reasons"].append("Aucun dénominateur logement disponible")
    quality["publishable"] = not quality["blocking_reasons"]
    provenance = {
        **provenance,
        "as_of": settings.as_of.isoformat(),
        "methodology_version": "1.0.0",
        "housing_mode": settings.housing_mode,
        "thresholds_per_1000_housing": [settings.tension_low, settings.tension_high],
        "household_size": settings.household_size if settings.housing_mode == "estimate" else None,
    }
    return BuildResult(
        frame,
        aggregate(frame, valid, "code_departement"),
        aggregate(frame, valid, "code_region"),
        quality,
        provenance,
        table_schema(),
    )


def aggregate(frame, members, key):
    lookup = dict(zip(frame.code_insee, frame[key]))
    result = []
    for code, group in frame.groupby(key, sort=True, dropna=False):
        local = [r for r in members if lookup[r["code"]] == code]
        companies = {r["siren"] for r in local}
        # Numérateur calculé sur les mêmes communes que chaque dénominateur.
        pop_codes = set(group.loc[group.population.notna() & (group.population > 0), "code_insee"])
        housing_codes = set(
            group.loc[group.nb_logements.notna() & (group.nb_logements > 0), "code_insee"]
        )
        pop = float(group.loc[group.code_insee.isin(pop_codes), "population"].sum())
        homes = float(group.loc[group.code_insee.isin(housing_codes), "nb_logements"].sum())
        row = {
            key: str(code),
            "nb_communes": len(group),
            "nb_entreprises_rge": len(companies),
            "nb_etablissements_rge": len({r["siret"] for r in local}),
            "population_connue": int(pop),
            "nb_logements_connus": round(homes, 6),
            "nb_communes_logements_manquants": int(group.nb_logements.isna().sum()),
            "ratio_artisans_1000_hab": ratio(
                len({r["siren"] for r in local if r["code"] in pop_codes}), pop
            ),
            "ratio_artisans_1000_logements": ratio(
                len({r["siren"] for r in local if r["code"] in housing_codes}), homes
            ),
            "communes_par_indice": {
                str(k): int(v) for k, v in group.indice_tension_rge.value_counts().items()
            },
        }
        if key == "code_region":
            row["nom_region"] = group.nom_region.iloc[0]
        for family in FAMILIES:
            row["nb_qualif_" + family] = len(
                {r["qualification"] for r in local if family in r["families"]}
            )
        result.append(row)
    return result


def table_schema():
    definitions = [
        ("code_insee", "string", "Code commune INSEE, conservant les zéros initiaux et la Corse."),
        ("nom_commune", "string", "Nom officiel de commune au jour de collecte API Geo."),
        ("code_departement", "string", "Code du département ; DOM sur trois caractères."),
        ("code_region", "string", "Code de région INSEE."),
        ("nom_region", "string", "Nom de la région dans l'API Geo."),
        (
            "population",
            "integer",
            "Population fournie par l'API Geo ; millésime non exposé, null si absent.",
        ),
        (
            "nb_logements",
            "number",
            "Parc INSEE tous logements ; proxy population/taille_ménage si estimation explicite. Null si manquant.",
        ),
        ("source_logements", "string", "INSEE, estimation_population, manquant ou fictif."),
        (
            "millesime_logements",
            "integer",
            "Année de référence du recensement INSEE, différente de l'année RGE.",
        ),
        (
            "nb_entreprises_rge",
            "integer",
            "SIREN distincts disposant d'au moins un établissement de travaux RGE actif dans la commune.",
        ),
        (
            "nb_etablissements_rge",
            "integer",
            "SIRET distincts de travaux RGE dont les dates englobent la date d'observation.",
        ),
        *[
            (
                "nb_qualif_" + f,
                "integer",
                f"Nombre de couples établissement-organisme-code-domaine distincts dans la famille {f}. Familles non exclusives.",
            )
            for f in FAMILIES
        ],
        (
            "ratio_artisans_1000_hab",
            "number",
            "1000 * entreprises RGE / population ; null si dénominateur nul ou absent.",
        ),
        (
            "ratio_artisans_1000_logements",
            "number",
            "1000 * entreprises RGE / logements ; null si dénominateur nul ou absent.",
        ),
        (
            "indice_tension_rge",
            "string",
            "Classe descriptive selon le ratio logements non arrondi. Seuils configurés dans provenance.json ; non officiel.",
        ),
        (
            "url_verification_locale",
            "string",
            "URL Legibat construite ; existence et disponibilité de cette page non attestées.",
        ),
        (
            "date_observation",
            "date",
            "Date de contrôle de validité des qualifications (bornes inclusives).",
        ),
        (
            "donnees_partielles",
            "boolean",
            "Vrai pour démonstration ou collecte limitée, dont la publication est interdite.",
        ),
    ]
    fields = []
    nullable = {
        "population",
        "nb_logements",
        "millesime_logements",
        "ratio_artisans_1000_hab",
        "ratio_artisans_1000_logements",
    }
    for name, kind, description in definitions:
        constraints = {"required": name not in nullable}
        if kind in {"number", "integer"}:
            constraints["minimum"] = 0
        if name == "code_insee":
            constraints.update(unique=True, pattern=r"^(?:[0-9]{5}|2[AB][0-9]{3})$")
        if name == "indice_tension_rge":
            constraints["enum"] = LABELS
        if name == "source_logements":
            constraints["enum"] = ["INSEE", "estimation_population", "manquant", "fictif"]
        field = {
            "name": name,
            "type": kind,
            "title": name.replace("_", " "),
            "description": description,
            "constraints": constraints,
        }
        if name == "url_verification_locale":
            field["format"] = "uri"
        if kind == "boolean":
            field.update(trueValues=["True", "true"], falseValues=["False", "false"])
        fields.append(field)
    return {"fields": fields, "primaryKey": ["code_insee"], "missingValues": [""]}


def export_dataset(result: BuildResult, output: Path, settings: Settings):
    dist = output / "dist_data"
    dist.mkdir(parents=True, exist_ok=True)
    result.frame.to_csv(
        dist / f"{settings.stem}.csv", sep=";", index=False, encoding="utf-8", lineterminator="\n"
    )
    result.frame.to_csv(
        dist / f"{settings.stem}-comma.csv",
        sep=",",
        index=False,
        encoding="utf-8",
        lineterminator="\n",
    )
    result.frame.to_parquet(
        dist / f"{settings.stem}.parquet", index=False, engine="pyarrow", compression="zstd"
    )
    write_json(dist / f"synthese-departementale-rge-{settings.year}.json", result.departments)
    write_json(dist / f"synthese-regionale-rge-{settings.year}.json", result.regions)
    write_json(dist / "schema.json", result.schema)
    write_json(dist / "quality-report.json", result.quality)
    write_json(dist / "provenance.json", result.provenance)
    names = [
        f"{settings.stem}.csv",
        f"{settings.stem}-comma.csv",
        f"{settings.stem}.parquet",
        f"synthese-departementale-rge-{settings.year}.json",
        f"synthese-regionale-rge-{settings.year}.json",
        "schema.json",
        "quality-report.json",
        "provenance.json",
    ]
    files = [dist / name for name in names]
    write_json(
        dist / "manifest.json",
        {f.name: {"sha256": sha256(f), "bytes": f.stat().st_size} for f in files},
    )
    return files + [dist / "manifest.json"]
