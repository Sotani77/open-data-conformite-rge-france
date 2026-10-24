"""Configuration explicite ; les secrets ne sont jamais sérialisés dans les exports."""

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def paris_today() -> date:
    return datetime.now(ZoneInfo("Europe/Paris")).date()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    as_of: date = Field(default_factory=paris_today)
    ademe_dataset_url: str = (
        "https://data.ademe.fr/data-fair/api/v1/datasets/liste-des-entreprises-rge-2"
    )
    geo_api_url: str = "https://geo.api.gouv.fr"
    insee_url: str = "https://www.insee.fr/fr/statistiques/fichier/2521169/comparateur.parquet"
    housing_file: Path | None = None
    housing_year: int = 2023
    housing_geo_year: int = 2026
    housing_mode: str = "insee"
    household_size: float = Field(default=2.2, gt=0)
    tension_low: float = Field(default=2.0, gt=0)
    tension_high: float = Field(default=5.0, gt=0)
    max_unmatched_fraction: float = Field(default=0.15, ge=0, le=1)
    max_invalid_fraction: float = Field(default=0.05, ge=0, le=1)
    max_conflicting_fraction: float = Field(default=0.001, ge=0, le=1)
    http_timeout: float = Field(default=60, gt=0)
    http_attempts: int = Field(default=4, ge=1, le=10)
    page_size: int = Field(default=1000, ge=1, le=10000)
    state_file: Path = Path(".state/publication.json")
    datagouv_api_url: str = "https://www.data.gouv.fr/api/1/"
    datagouv_api_key: SecretStr = SecretStr("")
    datagouv_org_id: str = ""
    datagouv_dataset_id: str = ""
    organization_name: str = "Legibat - Observatoire de la Conformité BTP"
    organization_siren: str = ""
    organization_siret: str = ""
    organization_logo_url: str = ""
    github_token: SecretStr = SecretStr("")
    github_owner: str = ""
    github_repo: str = "open-data-conformite-rge-france"
    github_branch: str = "main"
    github_transport: str = "api"
    github_api_url: str = "https://api.github.com/"
    github_api_version: str = "2026-03-10"
    site_url: str = "https://legibat.fr"
    methodology_url: str = "https://legibat.fr/etude-couverture-rge"
    enable_pages: bool = True

    @field_validator("housing_mode")
    @classmethod
    def housing_mode_valid(cls, value):
        if value not in {"insee", "estimate"}:
            raise ValueError("HOUSING_MODE doit être insee ou estimate")
        return value

    @field_validator("github_transport")
    @classmethod
    def github_transport_valid(cls, value):
        if value not in {"api", "git"}:
            raise ValueError("GITHUB_TRANSPORT doit être api ou git")
        return value

    @field_validator("organization_siren")
    @classmethod
    def siren_valid(cls, value):
        if value and (not value.isdigit() or len(value) != 9):
            raise ValueError("ORGANIZATION_SIREN doit contenir 9 chiffres")
        return value

    @field_validator("organization_siret")
    @classmethod
    def siret_valid(cls, value):
        if value and (not value.isdigit() or len(value) != 14):
            raise ValueError("ORGANIZATION_SIRET doit contenir 14 chiffres")
        return value

    @field_validator("github_repo", "github_owner")
    @classmethod
    def github_component(cls, value):
        import re

        if value and not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
            raise ValueError("Nom GitHub invalide")
        return value

    @field_validator(
        "ademe_dataset_url",
        "geo_api_url",
        "insee_url",
        "datagouv_api_url",
        "github_api_url",
        "site_url",
        "methodology_url",
        "organization_logo_url",
    )
    @classmethod
    def https_url(cls, value):
        from urllib.parse import urlsplit

        if value:
            parts = urlsplit(value)
            if parts.scheme != "https" or not parts.netloc or parts.username or parts.password:
                raise ValueError("URL HTTPS sans identifiants requise")
        return value

    @model_validator(mode="after")
    def ordered_thresholds(self):
        if self.tension_high <= self.tension_low:
            raise ValueError("TENSION_HIGH doit être supérieur à TENSION_LOW")
        if (
            self.organization_siren
            and self.organization_siret
            and not self.organization_siret.startswith(self.organization_siren)
        ):
            raise ValueError("Le SIRET doit commencer par le SIREN de l'organisation")
        return self

    @property
    def year(self):
        return self.as_of.year

    @property
    def title(self):
        return (
            f"Cartographie {self.year} de la couverture d'artisans RGE "
            "par commune vs nombre de logements"
        )

    @property
    def stem(self):
        return f"cartographie-{self.year}-couverture-artisans-rge-communes"

    def require_publication(self):
        missing = []
        for name in ("datagouv_api_key", "github_token"):
            if not getattr(self, name).get_secret_value():
                missing.append(name.upper())
        if not self.github_owner:
            missing.append("GITHUB_OWNER")
        if missing:
            raise ValueError("Configuration de publication manquante : " + ", ".join(missing))
