"""Publication idempotente sur l'API REST v1 data.gouv.fr."""

import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlsplit

from common import Api, PipelineError, State, sha256

TAGS = [
    "rge",
    "renovation-energetique",
    "batiment",
    "artisans",
    "logement",
    "photovoltaique",
    "ademe",
    "travaux",
    "urbanisme",
]


class DataGouvClient(Api):
    def __init__(self, settings, http, state: State):
        super().__init__(settings.datagouv_api_url, http)
        self.settings, self.state = settings, state

    def pages(self, path, params=None):
        seen = set()
        while path:
            if path in seen:
                raise PipelineError("Boucle de pagination data.gouv.fr")
            seen.add(path)
            page = self.call("GET", path, params=params)
            yield from page["data"]
            path, params = page.get("next_page"), None

    def preflight(self):
        self.call("GET", "me/")

    def ensure_organization(self):
        s = self.settings
        identifier = s.datagouv_org_id or self.state.data.get("organization_id")
        if identifier:
            org = self.call("GET", f"organizations/{identifier}/")
        else:
            params = {"q": s.organization_name, "page_size": 100}
            if s.organization_siret:
                params = {"business_number_id": s.organization_siret, "page_size": 100}
            candidates = [
                o
                for o in self.pages("organizations/", params)
                if (
                    o.get("business_number_id") == s.organization_siret
                    if s.organization_siret
                    else str(o.get("business_number_id") or "").startswith(s.organization_siren)
                    if s.organization_siren
                    else o.get("name") == s.organization_name
                )
            ]
            if len(candidates) > 1:
                raise PipelineError(
                    "Plusieurs organisations correspondent : renseigner DATAGOUV_ORG_ID"
                )
            if candidates:
                org = self.call("GET", f"organizations/{candidates[0]['id']}/")
            else:
                if not s.organization_siret or not s.organization_logo_url:
                    raise PipelineError(
                        "Création d'organisation : ORGANIZATION_SIRET et ORGANIZATION_LOGO_URL requis"
                    )
                org = self.call(
                    "POST",
                    "organizations/",
                    json={
                        "name": s.organization_name,
                        "business_number_id": s.organization_siret,
                        "url": s.site_url,
                        "image_url": s.organization_logo_url,
                        "description": "Observatoire privé porté par Legibat. Production d'indicateurs ouverts sur la couverture territoriale des professionnels RGE. Sources et méthodes publiées sur "
                        + s.methodology_url,
                    },
                )
                # La réponse de création peut précéder l'ajout des droits du membre.
                # Conserver l'identifiant puis relire l'objet avant de contrôler les droits.
                self.state.data["organization_id"] = org["id"]
                self.state.save()
                org = self.call("GET", f"organizations/{org['id']}/")
        remote_number = org.get("business_number_id") or ""
        if s.organization_siret and remote_number and remote_number != s.organization_siret:
            raise PipelineError("Le SIRET de l'organisation diffère de la configuration")
        if (
            s.organization_siren
            and remote_number
            and not remote_number.startswith(s.organization_siren)
        ):
            raise PipelineError("Le SIREN de l'organisation diffère de la configuration")
        if org.get("permissions", {}).get("edit") is False:
            raise PipelineError("Le compte n'a pas le droit de modifier cette organisation")
        self.state.data["organization_id"] = org["id"]
        self.state.save()
        return org

    def upsert_dataset(self, org, description):
        s = self.settings
        key = f"legibat-rge-communes-{s.year}"
        dataset_id = s.datagouv_dataset_id or self.state.data.get("datasets", {}).get(str(s.year))
        found = None
        if dataset_id:
            found = self.call("GET", f"datasets/{dataset_id}/")
            if (found.get("organization") or {}).get("id") != org["id"]:
                raise PipelineError("Le dataset n'appartient pas à l'organisation configurée")
        else:
            candidates = [
                d
                for d in self.pages(f"organizations/{org['id']}/datasets/", {"page_size": 100})
                if d.get("extras", {}).get("legibat_pipeline_key") == key
                or d.get("title") == s.title
            ]
            if len(candidates) > 1:
                raise PipelineError(
                    "Plusieurs datasets correspondent : renseigner DATAGOUV_DATASET_ID"
                )
            if candidates:
                found = self.call("GET", f"datasets/{candidates[0]['id']}/")
        payload = {
            "title": s.title,
            "description": description,
            "organization": org["id"],
            "tags": TAGS,
            "license": "lov2",
            "frequency": "monthly",
            "private": False,
            "spatial": {"zones": ["country:fr"], "granularity": "fr:commune"},
            "temporal_coverage": {"start": s.as_of.isoformat(), "end": s.as_of.isoformat()},
            "extras": {
                **((found or {}).get("extras") or {}),
                "legibat_pipeline_key": key,
                "methodology_url": s.methodology_url,
            },
        }
        dataset = (
            self.call("PUT", f"datasets/{found['id']}/", json=payload)
            if found
            else self.call("POST", "datasets/", json=payload)
        )
        self.state.data.setdefault("datasets", {})[str(s.year)] = dataset["id"]
        self.state.save()
        return dataset

    def _existing_resource(self, dataset, key, filename):
        cached = self.state.data.get("resources", {}).get(dataset["id"], {}).get(key, {})
        hits = []
        for r in dataset.get("resources", []):
            if (
                r["id"] == cached.get("id")
                or (r.get("extras") or {}).get("legibat_pipeline_key") == key
                or unquote(urlsplit(r.get("url", "")).path).rsplit("/", 1)[-1] == filename
                or r.get("title") == filename
            ):
                hits.append(r)
        if len(hits) > 1:
            raise PipelineError(
                f"Ressources ambiguës pour {key} ; corriger les doublons avant relance"
            )
        return hits[0] if hits else None

    def _upload_file(self, dataset, path: Path, schema_url=None):
        key = path.name
        current = self._existing_resource(dataset, key, path.name)
        states = self.state.data.setdefault("resources", {}).setdefault(dataset["id"], {})
        digest = sha256(path)
        unchanged = (
            current
            and states.get(key, {}).get("sha256") == digest
            and states.get(key, {}).get("url") == current.get("url")
        )
        if not unchanged:
            route = (
                f"datasets/{dataset['id']}/resources/{current['id']}/upload/"
                if current
                else f"datasets/{dataset['id']}/upload/"
            )
            with path.open("rb") as f:
                current = self.call(
                    "POST",
                    route,
                    files={
                        "file": (
                            path.name,
                            f,
                            mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                        )
                    },
                )
            if not current.get("id") or not current.get("url"):
                raise PipelineError("Réponse d'upload sans identifiant/URL")
            # Sauvegarde immédiatement : reprise possible si l'étape métadonnées échoue.
            states[key] = {"id": current["id"], "sha256": digest, "url": current["url"]}
            self.state.save()
        fmt = path.suffix.lstrip(".")
        meta = {
            "title": path.name,
            "type": "documentation"
            if path.name
            in {"schema.json", "quality-report.json", "provenance.json", "manifest.json"}
            else "main",
            "format": fmt,
            "filetype": "file",
            "url": current["url"],
            "description": "Export agrégé Legibat. Méthode, contrôles et sources documentés dans le jeu de données.",
            "extras": {**(current.get("extras") or {}), "legibat_pipeline_key": key},
        }
        if schema_url and fmt in {"csv", "parquet"}:
            meta["schema"] = {"url": schema_url}
        resource = self.call(
            "PUT", f"datasets/{dataset['id']}/resources/{current['id']}/", json=meta
        )
        dataset["resources"] = [
            r for r in dataset.get("resources", []) if r["id"] != resource["id"]
        ] + [resource]
        return resource

    def upload_resources(self, dataset, files):
        # Relecture : indispensable pour les relances après une réponse d'écriture perdue.
        dataset = self.call("GET", f"datasets/{dataset['id']}/")
        schema_path = next(p for p in files if p.name == "schema.json")
        schema = self._upload_file(dataset, schema_path)
        resources = [schema]
        for path in files:
            if path != schema_path:
                resources.append(
                    self._upload_file(dataset, path, schema.get("latest") or schema["url"])
                )
        key = "legibat-methodology"
        docs = [
            r
            for r in dataset.get("resources", [])
            if (r.get("extras") or {}).get("legibat_pipeline_key") == key
            or r.get("url") == self.settings.methodology_url
        ]
        if len(docs) > 1:
            raise PipelineError("Plusieurs ressources méthodologiques identiques")
        payload = {
            "title": "Documentation méthodologique Legibat",
            "type": "documentation",
            "format": "html",
            "filetype": "remote",
            "url": self.settings.methodology_url,
            "description": "Méthode de calcul, sources, seuils et limites de l'indice de couverture RGE.",
            "extras": {"legibat_pipeline_key": key},
        }
        route = f"datasets/{dataset['id']}/resources/" + (f"{docs[0]['id']}/" if docs else "")
        resources.append(self.call("PUT" if docs else "POST", route, json=payload))
        return resources
