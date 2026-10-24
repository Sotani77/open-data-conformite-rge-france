"""Entrée CLI : génération locale ou publication explicite, avec reprise après échec."""

import argparse
import logging
import shutil
import sys
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from common import Http, PipelineError, State, write_json
from config import Settings, paris_today
from datagouv_client import DataGouvClient
from dataset_builder import build_dataset, export_dataset, fetch_live, sample_inputs
from github_publisher import GitHubPublisher, source_files
from landing_page_generator import generate_landing
from metadata import description, generate_metadata

LOG = logging.getLogger("legibat")


def parser():
    p = argparse.ArgumentParser(description="Couverture RGE : ADEME × API Geo × logements INSEE")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run", action="store_true", help="Générer les fichiers, sans aucune écriture distante"
    )
    mode.add_argument("--publish", action="store_true", help="Publier sur data.gouv.fr et GitHub")
    p.add_argument("--source", choices=["sample", "live"], default="sample")
    p.add_argument("--output", type=Path, default=Path("."))
    p.add_argument("--as-of", type=date.fromisoformat)
    p.add_argument("--max-rge-rows", type=int, help="Limiter l'aperçu API ; interdit pour publier")
    p.add_argument("--housing-mode", choices=["insee", "estimate"])
    p.add_argument("--housing-file", type=Path)
    return p


def run(args):
    overrides = {
        k: v
        for k, v in {
            "as_of": args.as_of,
            "housing_mode": args.housing_mode,
            "housing_file": args.housing_file,
        }.items()
        if v is not None
    }
    settings = Settings(**overrides)
    if args.max_rge_rows is not None and args.max_rge_rows < 1:
        raise PipelineError("--max-rge-rows doit être positif")
    if args.publish and (args.source != "live" or args.max_rge_rows is not None):
        raise PipelineError("Publication réservée à --source live sans limite de lignes")
    if args.publish and settings.as_of != paris_today():
        raise PipelineError(
            "La source courante ne permet pas une publication historique : utiliser la date du jour"
        )
    if args.publish:
        settings.require_publication()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source_root = Path(__file__).resolve().parent
    if output != source_root:
        for path in source_files(source_root):
            target = output / path.relative_to(source_root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    http = Http(settings.http_timeout, settings.http_attempts)
    try:
        LOG.info("Collecte : %s ; observation : %s", args.source, settings.as_of)
        inputs = (
            sample_inputs(settings)
            if args.source == "sample"
            else fetch_live(settings, output / "work/raw", http, args.max_rge_rows)
        )
    finally:
        http.close()
    result = build_dataset(*inputs, settings)
    files = export_dataset(result, output, settings)
    publication = {"status": "local", "dry_run": args.dry_run}

    def render():
        artifacts = generate_metadata(result, output, settings, files, publication)
        artifacts += generate_landing(result, output, settings, publication)
        write_json(output / "publication.json", publication)
        return files + artifacts + [output / "publication.json"] + source_files(output)

    generated = render()
    LOG.info(
        "Exports : %s communes ; %s établissements rattachés",
        len(result.frame),
        result.quality["counts"]["matched_establishments"],
    )
    if args.dry_run:
        LOG.info("Dry-run terminé : %s. Aucune publication effectuée.", output)
        return publication
    if not result.quality["publishable"]:
        raise PipelineError(
            "Publication bloquée par les contrôles : "
            + "; ".join(result.quality["blocking_reasons"])
        )
    state_path = (
        settings.state_file if settings.state_file.is_absolute() else output / settings.state_file
    )
    state = State(state_path, settings.datagouv_api_url)
    dg_http = Http(
        settings.http_timeout,
        settings.http_attempts,
        headers={"X-API-KEY": settings.datagouv_api_key.get_secret_value()},
    )
    gh_http = Http(
        settings.http_timeout,
        settings.http_attempts,
        headers={
            "Authorization": "Bearer " + settings.github_token.get_secret_value(),
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": settings.github_api_version,
        },
    )
    dg = DataGouvClient(settings, dg_http, state)
    gh = GitHubPublisher(settings, gh_http)
    try:
        dg.preflight()
        gh.preflight()
        org = dg.ensure_organization()
        repository = gh.ensure_repository()
        dataset = dg.upsert_dataset(org, description(result, settings))
        publication.update(
            status="data-gouv-created",
            dataset_id=dataset["id"],
            dataset_url=dataset.get("page"),
            github_url=repository["html_url"],
        )
        write_json(output / "publication.json", publication)
        resources = dg.upload_resources(dataset, files)
        # Ne jamais sérialiser toutes les réponses API : uniquement les champs publics utiles.
        publication["resources"] = [
            {
                k: r[k]
                for k in ("id", "title", "format", "type", "url", "latest", "preview_url")
                if k in r
            }
            for r in resources
        ]
        publication["status"] = "data-gouv-published"
        generated = render()
        committed = gh.sync(output, generated, f"Mettre à jour les données RGE au {settings.as_of}")
        publication.update(github_data_commit=committed["sha"], status="published")
        if settings.enable_pages:
            pages = gh.enable_pages()
            publication.update(pages_url=pages["url"], pages_status=pages["status"])
        generated = render()
        # La seconde synchronisation relie l'URL Pages retournée par l'API. Aucun polling ni faux délai garanti.
        final = gh.sync(output, generated, "Relier les publications et documenter leur état")
        LOG.info(
            "Publication terminée ; commit %s ; Pages : %s",
            final["sha"],
            publication.get("pages_status", "désactivé"),
        )
        return publication
    except Exception as exc:
        publication.update(status="incomplete", error_type=type(exc).__name__)
        write_json(output / "publication.json", publication)
        raise
    finally:
        dg_http.close()
        gh_http.close()


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        run(parser().parse_args(argv))
    except ValidationError as exc:
        # Ne pas imprimer les input_value de Pydantic, susceptibles de contenir des secrets.
        LOG.error(
            "Configuration invalide : %s",
            ", ".join(".".join(map(str, e["loc"])) for e in exc.errors()),
        )
        return 2
    except (PipelineError, ValueError, OSError) as exc:
        LOG.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
