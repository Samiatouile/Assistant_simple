"""Chargement de la configuration (config.yaml + variables d'environnement).

Aucune cle API n'est jamais lue en dur: tout passe par .env (optionnel).
Le MVP fonctionne entierement sans LLM ni cle.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import yaml

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # dotenv est optionnel
    pass

ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT_DIR / "config.yaml"


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def load_config() -> Dict[str, Any]:
    """Charge config.yaml puis applique les surcharges .env."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        cfg: Dict[str, Any] = yaml.safe_load(fh)

    # Surcharges via environnement (jamais de secret dans config.yaml)
    cfg.setdefault("retrieval", {})["use_embeddings"] = _as_bool(
        os.getenv("USE_EMBEDDINGS"), cfg.get("retrieval", {}).get("use_embeddings", False)
    )

    llm = cfg.setdefault("llm", {})
    llm["enabled"] = _as_bool(os.getenv("LLM_ENABLED"), llm.get("enabled", False))
    llm["provider"] = os.getenv("LLM_PROVIDER", llm.get("provider", "none"))

    cfg["_root_dir"] = str(ROOT_DIR)
    return cfg


def abspath(relative: str) -> Path:
    """Resout un chemin relatif du config par rapport a la racine projet."""
    return ROOT_DIR / relative


def get_env(key: str, default: str | None = None) -> str | None:
    return os.getenv(key, default)
