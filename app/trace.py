"""Observabilite: creation de traces locales JSONL, avec masquage des secrets.

Aucune donnee sensible (mot de passe, OTP, code PIN, numero de carte complet)
n'est jamais stockee en clair.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from app.config import abspath, load_config

# Motifs de donnees sensibles a masquer dans toute trace.
_SENSITIVE_PATTERNS = [
    (re.compile(r"\b(?:otp|code\s*pin|pin|mot\s*de\s*passe|password|mdp|cvv|cvc)\b\s*[:=]?\s*\w+",
                re.IGNORECASE), "[MASQUE_SECRET]"),
    (re.compile(r"\b(?:\d[ -]?){12,19}\b"), "[MASQUE_CARTE]"),  # numero de carte
]


def _mask(text: Any, digit_run_min: int = 6) -> Any:
    if not isinstance(text, str):
        return text
    out = text
    for pattern, repl in _SENSITIVE_PATTERNS:
        out = pattern.sub(repl, out)
    # Sequences longues de chiffres (OTP, RIB, numero de compte)
    out = re.sub(rf"\b\d{{{digit_run_min},}}\b", "[MASQUE_CHIFFRES]", out)
    return out


def new_trace_id() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mask_deep(obj: Any, digit_run_min: int) -> Any:
    if isinstance(obj, str):
        return _mask(obj, digit_run_min)
    if isinstance(obj, dict):
        return {k: _mask_deep(v, digit_run_min) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mask_deep(v, digit_run_min) for v in obj]
    return obj


def write_trace(trace: Dict[str, Any]) -> Path:
    """Ecrit une trace masquee dans traces/<trace_id>.json et append au journal."""
    cfg = load_config()
    digit_min = cfg.get("security", {}).get("mask_digit_run_min", 6)
    traces_dir = abspath(cfg["paths"]["traces_dir"])
    traces_dir.mkdir(parents=True, exist_ok=True)

    masked = _mask_deep(trace, digit_min)
    tid = masked.get("trace_id", new_trace_id())

    # Fichier unitaire par trace
    fpath = traces_dir / f"{tid}.json"
    with open(fpath, "w", encoding="utf-8") as fh:
        json.dump(masked, fh, ensure_ascii=False, indent=2)

    # Journal append-only (JSONL) pour analyse d'anomalies
    with open(traces_dir / "journal.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps(masked, ensure_ascii=False) + "\n")

    return fpath
