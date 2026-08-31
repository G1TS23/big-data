"""Chargement de la configuration : variables d'environnement et flux déclarés."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    salt: str
    source_path: Path
    lake_path: Path
    ch_host: str
    ch_port: int
    ch_user: str
    ch_password: str
    ch_pilotage_password: str
    ch_recherche_password: str
    k_anonymite: int

    @property
    def quarantine_path(self) -> Path:
        return self.lake_path / "_quarantaine"


def load_settings(env_file: str | os.PathLike[str] | None = None) -> Settings:
    load_dotenv(env_file or ROOT / ".env")

    salt = os.getenv("EDS_SALT", "")
    if not salt or salt.startswith("remplacer"):
        raise ConfigError(
            "EDS_SALT absent ou non renseigné. Copier .env.example en .env puis "
            'générer un sel : python3 -c "import secrets; print(secrets.token_hex(32))"'
        )
    if len(salt) < 32:
        raise ConfigError("EDS_SALT trop court : au moins 32 caractères attendus.")

    source = Path(os.getenv("EDS_SOURCE_PATH", "")).expanduser()
    if not source.is_absolute():
        source = (ROOT / source).resolve()
    if not source.is_dir():
        raise ConfigError(
            f"EDS_SOURCE_PATH introuvable : {source}\n"
            "Renseigner dans .env l'emplacement du dépôt du CHU (voir README)."
        )

    lake = Path(os.getenv("EDS_LAKE_PATH", "./lake")).expanduser()
    if not lake.is_absolute():
        lake = (ROOT / lake).resolve()

    return Settings(
        salt=salt,
        source_path=source,
        lake_path=lake,
        ch_host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        ch_port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        ch_user=os.getenv("CLICKHOUSE_ADMIN_USER", "default"),
        ch_password=os.getenv("CLICKHOUSE_ADMIN_PASSWORD", ""),
        ch_pilotage_password=os.getenv("CLICKHOUSE_PILOTAGE_PASSWORD", ""),
        ch_recherche_password=os.getenv("CLICKHOUSE_RECHERCHE_PASSWORD", ""),
        k_anonymite=int(os.getenv("EDS_K_ANONYMITE", "5")),
    )


def load_sources(path: str | os.PathLike[str] | None = None) -> dict:
    with open(path or ROOT / "config" / "sources.yml", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    sources = cfg.get("sources")
    if not sources:
        raise ConfigError("config/sources.yml ne déclare aucune source.")
    _validate(sources)
    return sources


def _validate(sources: dict) -> None:
    """Garantit qu'aucune colonne déclarée comme supprimée ne ressort dans le lake.

    Ce contrôle transforme la déclaration de confidentialité en invariant
    vérifié au démarrage, plutôt qu'en simple commentaire.
    """
    for name, spec in sources.items():
        privacy = spec.get("privacy") or {}
        dropped = set(privacy.get("drop") or [])
        exposed = set(spec.get("lake_columns") or [])
        leaked = dropped & exposed
        if leaked:
            raise ConfigError(
                f"source '{name}' : colonne(s) déclarée(s) supprimée(s) mais "
                f"présente(s) dans lake_columns : {sorted(leaked)}"
            )
