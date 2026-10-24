"""Pages autonomes et fragment HTML pour Legibat ; aucun faux identifiant public."""

import html
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from common import write_text
from metadata import format_fr, jsonld


def safe_jsonld(data):
    return (
        json.dumps(data, ensure_ascii=False, allow_nan=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def official_iframe(publication, datagouv_api_url):
    dataset_id = publication.get("dataset_id", "")
    if not re.fullmatch(r"[a-fA-F0-9]{24}", dataset_id):
        return (
            "<p>La prévisualisation officielle sera ajoutée après publication sur data.gouv.fr.</p>"
        )
    p = urlsplit(datagouv_api_url)
    base = f"{p.scheme}://{p.netloc}"
    # Carte officielle du dataset. Une URL preview_url officielle peut fournir
    # l'explorateur tabulaire après traitement asynchrone du CSV par data.gouv.fr.
    preview = next(
        (
            r.get("preview_url")
            for r in publication.get("resources", [])
            if r.get("format") == "csv" and r.get("preview_url")
        ),
        None,
    )
    if preview:
        parsed = urlsplit(preview)
        if parsed.scheme != "https" or not (
            parsed.hostname == "data.gouv.fr" or (parsed.hostname or "").endswith(".data.gouv.fr")
        ):
            preview = None
    src = preview or f"{base}/embeds/datasets/{dataset_id}"
    return f'<iframe title="Jeu de données RGE sur data.gouv.fr" src="{html.escape(src, quote=True)}" loading="lazy" width="100%" height="520" style="border:1px solid #d6dfdb;border-radius:12px"></iframe>'


STYLE = """
:root{color-scheme:light;--ink:#17332e;--accent:#267665;--paper:#f7f8f5}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:17px/1.65 system-ui,sans-serif}
main{max-width:1120px;margin:auto;padding:56px 24px}h1{font-size:clamp(30px,4.5vw,54px);line-height:1.12;max-width:1000px;margin:18px 0 24px;letter-spacing:-1.4px}
h2{margin-top:48px;font-size:27px}a{color:#175c50;text-underline-offset:4px}p{max-width:85ch}
.eyebrow{text-transform:uppercase;letter-spacing:2px;font-size:12px;font-weight:700}.note{border-left:4px solid #c47d32;background:#fff4df;padding:16px 20px}
.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:30px 0}.metric{padding:22px;border:1px solid #d6dfdb;border-radius:12px;background:white}.metric strong{display:block;font-size:32px}.metric span{font-size:14px}
.table-scroll{overflow:auto}table{width:100%;border-collapse:collapse;background:white;font-size:14px}th,td{text-align:left;border-bottom:1px solid #e0e7e3;padding:12px}th{background:#e9efeb}figure{margin:24px 0}img{max-width:100%;height:auto}figcaption,footer{font-size:14px;color:#52675f}nav{display:flex;flex-wrap:wrap;gap:20px}code{font-size:14px;overflow-wrap:anywhere}
@media(max-width:650px){.metrics{grid-template-columns:1fr}main{padding:28px 18px}}
"""


def generate_landing(result, output: Path, settings, publication):
    data = jsonld(result, settings, publication)
    q = result.quality["counts"]
    partial = result.provenance["partial"]
    note = (
        '<p class="note"><strong>Démonstration / échantillon.</strong> Ces valeurs ne décrivent pas la couverture réelle du territoire.</p>'
        if partial
        else '<p class="note">Les classes décrivent une présence locale. Elles ne mesurent ni la disponibilité des entreprises ni les délais de chantier.</p>'
    )
    rows = "".join(
        f"<tr><td>{html.escape(r['nom_region'])}</td><td>{format_fr(r['nb_communes'])}</td><td>{format_fr(r['nb_entreprises_rge'])}</td><td>{format_fr(r['nb_etablissements_rge'])}</td><td>{format_fr(r['ratio_artisans_1000_logements'], 3)}</td></tr>"
        if r["ratio_artisans_1000_logements"] is not None
        else f"<tr><td>{html.escape(r['nom_region'])}</td><td>{r['nb_communes']}</td><td>{r['nb_entreprises_rge']}</td><td>{r['nb_etablissements_rge']}</td><td>ND</td></tr>"
        for r in result.regions
    )
    dictionary = "".join(
        f"<tr><td><code>{f['name']}</code></td><td>{html.escape(f['description'])}</td></tr>"
        for f in result.schema["fields"]
    )
    downloads = "".join(
        f'<li><a href="{html.escape(r.get("latest") or r["url"], quote=True)}">{html.escape(r["title"])}</a></li>'
        for r in publication.get("resources", [])
        if r.get("type") == "main"
    )
    download_section = (
        f"<ul>{downloads}</ul>"
        if downloads
        else "<p>Les fichiers sont fournis dans le dossier <code>dist_data</code> du projet local.</p>"
    )
    housing_text = (
        f"Logements INSEE {settings.housing_year}, géographie {settings.housing_geo_year}. Les valeurs manquantes restent absentes."
        if settings.housing_mode == "insee"
        else f"Proxy : population divisée par {settings.household_size}. Il ne représente pas le parc total de logements."
    )
    intro = f"""<article>
<p class="eyebrow">Legibat · Observatoire de la couverture RGE · {settings.year}</p>
<h1>{html.escape(data["name"])}</h1>
<p>Rapprocher les professionnels de travaux RGE et les logements pour explorer les écarts de présence locale. Observation au {settings.as_of.isoformat()}.</p>
{note}
<div class="metrics"><div class="metric"><strong>{format_fr(q["communes"])}</strong><span>communes dans le périmètre</span></div><div class="metric"><strong>{format_fr(q["matched_companies"])}</strong><span>entreprises distinctes rattachées</span></div><div class="metric"><strong>{format_fr(q["matched_establishments"])}</strong><span>établissements rattachés</span></div></div>
<h2>La couverture par région ou territoire</h2>
<div class="table-scroll"><table><caption>Entreprises RGE pour 1 000 logements, sur les communes au dénominateur connu.</caption><thead><tr><th scope="col">Région</th><th scope="col">Communes</th><th scope="col">Entreprises</th><th scope="col">Établissements</th><th scope="col">Ratio logements</th></tr></thead><tbody>{rows}</tbody></table></div>
<h2>Comment lire l'indice</h2>
<p>Les entreprises sont dédupliquées par SIREN, les établissements par SIRET. Les qualifications doivent être valides à la date d'observation. Les activités d'étude, d'audit et d'architecture sont exclues. Un code postal partagé n'est jamais affecté à plusieurs communes sans désambiguïsation.</p>
<p>{html.escape(housing_text)} Le ratio vaut 1 000 × entreprises / logements. Les classes sont : zéro entreprise, « Désert RGE » ; ratio positif sous {settings.tension_low:g}, « Sous-capacité » ; de {settings.tension_low:g} inclus à {settings.tension_high:g} exclu, « Équilibré » ; à partir de {settings.tension_high:g}, « Forte couverture ». Sans logements positifs connus : « Non calculable ».</p>
<p>Ces seuils éditoriaux ne sont pas une norme publique. Une entreprise peut intervenir hors de sa commune et posséder plusieurs implantations. Les effectifs, carnets de commandes, déplacements et besoins de rénovation ne sont pas connus. Les agrégats recomptent les entreprises distinctes ; ils ne sont pas une somme des compteurs communaux.</p>
<p>Un statut RGE ne démontre ni l'existence d'une assurance décennale valable pour le chantier, ni la conformité Consuel, ni l'absence de fraude. Aucun délai d'intervention n'est calculé. Les URL locales suivent une convention de routage et ne prouvent pas l'existence de toutes les pages.</p>
<h2>Sources et qualité</h2><p><a href="{html.escape(settings.ademe_dataset_url, quote=True)}">ADEME : entreprises RGE</a> · <a href="{html.escape(settings.geo_api_url, quote=True)}">API Geo : communes et population</a> · <a href="https://www.insee.fr/fr/statistiques/2521169">INSEE : comparateur de territoires</a>. Sources collectées et transformées par Legibat, producteur privé indépendant.</p>
<p>{format_fr(q.get("unmatched_rows", 0))} lignes éligibles n'ont pas pu être rattachées. {format_fr(q.get("communes_missing_housing", 0))} communes ont un dénominateur logement manquant. Les contrôles détaillés et les empreintes de fichiers sont fournis avec les données. Aucun score de qualité, délai d'indexation ou référencement par un portail tiers n'est garanti.</p>
<h2>Télécharger et réutiliser</h2>{download_section}
<h2>Aperçu officiel</h2>{official_iframe(publication, settings.datagouv_api_url)}
<h2>Dictionnaire des variables</h2><div class="table-scroll"><table><thead><tr><th scope="col">Variable</th><th scope="col">Définition</th></tr></thead><tbody>{dictionary}</tbody></table></div>
<footer><p>Données sous <a href="https://www.etalab.gouv.fr/licence-ouverte-open-licence/">Licence Ouverte 2.0</a>. Citer ADEME, API Geo, INSEE et les transformations Legibat. <a href="{html.escape(settings.site_url, quote=True)}">Service Legibat</a>.</p></footer>
</article>"""
    script = '<script type="application/ld+json">' + safe_jsonld(data) + "</script>"
    fragment = intro + "\n" + script
    write_text(output / "landing/legibat-fragment.html", fragment)
    canonical = (
        f'<link rel="canonical" href="{html.escape(settings.methodology_url, quote=True)}">'
        if publication.get("dataset_url")
        else ""
    )
    robots = (
        '<meta name="robots" content="noindex,nofollow">'
        if partial or not publication.get("dataset_url")
        else ""
    )
    head = f'<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(data["name"])}</title><meta name="description" content="{html.escape(data["description"], quote=True)}">{canonical}{robots}<style>{STYLE}</style></head><body><main>'
    write_text(output / "landing/legibat.html", head + fragment + "</main></body></html>")
    chart = '<figure><img src="assets/couverture-regions.svg" alt="Entreprises RGE pour 1 000 logements par région"><figcaption>Les valeurs détaillées sont disponibles dans le tableau ci-dessus.</figcaption></figure>'
    pages_body = intro.replace("<h2>Comment lire", chart + "<h2>Comment lire")
    write_text(output / "docs/index.html", head + pages_body + script + "</main></body></html>")
    write_text(output / "docs/.nojekyll", "")
    return [
        output / p
        for p in [
            "landing/legibat-fragment.html",
            "landing/legibat.html",
            "docs/index.html",
            "docs/.nojekyll",
        ]
    ]
