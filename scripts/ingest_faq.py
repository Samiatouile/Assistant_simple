"""Ingestion de la FAQ Excel officielle -> data/processed/faq.jsonl.

- Privilegie la feuille 'All' si elle est complete.
- Sinon consolide les feuilles par domaine.
- Cree un identifiant stable par ligne (hash du contenu).
- Conserve toutes les metadonnees (View, Tags iOS/Android, orientation...).

Usage:
    python scripts/ingest_faq.py [chemin_vers_faq.xlsx]
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import abspath, load_config  # noqa: E402

# Colonnes attendues (tolerance sur accents/casse).
COLS = {
    "domaine": ["Domaine"],
    "motif": ["Motif de contact"],
    "sous_motif": ["Sous-motif"],
    "question": ["Question type"],
    "reponse": ["Réponse officielle", "Reponse officielle"],
    "variante_1": ["Variante 1"],
    "variante_2": ["Variante 2"],
    "variante_3": ["Variante 3"],
    "orientation": ["Orientation de la réponse", "Orientation de la reponse"],
    "view": ["View"],
    "tags_ios": ["Tags iOS"],
    "tags_android": ["Tags Android"],
}


def _clean(val):
    if val is None:
        return None
    s = str(val).strip()
    if s == "" or s.lower() == "nan":
        return None
    return s


def _find_faq_file(cfg) -> Path:
    raw_dir = abspath(cfg["paths"]["faq_raw_dir"])
    xlsx = sorted(raw_dir.glob("*.xlsx"))
    if not xlsx:
        raise FileNotFoundError(
            f"Aucun fichier .xlsx dans {raw_dir}. Deposez la FAQ officielle "
            f"(ex: '[Fr] FAQ Assistant virtuel_Consolidation_VF.xlsx')."
        )
    # priorite au fichier contenant 'FAQ' dans le nom
    for p in xlsx:
        if "faq" in p.name.lower():
            return p
    return xlsx[0]


def _pick_col(df: pd.DataFrame, names):
    for n in names:
        if n in df.columns:
            return n
    return None


def _rows_from_sheet(df: pd.DataFrame) -> list[dict]:
    colmap = {key: _pick_col(df, names) for key, names in COLS.items()}
    required = ["domaine", "motif", "sous_motif", "question", "reponse", "orientation"]
    if any(colmap[k] is None for k in required):
        return []
    rows = []
    for _, r in df.iterrows():
        rec = {key: (_clean(r[col]) if col else None) for key, col in colmap.items()}
        if not rec["question"] or not rec["reponse"]:
            continue
        rows.append(rec)
    return rows


def _stable_id(rec: dict) -> str:
    basis = "|".join([
        rec.get("domaine") or "", rec.get("motif") or "",
        rec.get("sous_motif") or "", rec.get("question") or "",
    ])
    h = hashlib.md5(basis.encode("utf-8")).hexdigest()[:8]
    return f"FAQ-{h}"


def ingest(path: str | None = None) -> Path:
    cfg = load_config()
    faq_path = Path(path) if path else _find_faq_file(cfg)
    print(f"[ingest] Lecture: {faq_path}")
    xls = pd.ExcelFile(faq_path)

    preferred = cfg["ingestion"]["preferred_sheet"]
    min_rows = cfg["ingestion"]["min_rows_all_sheet"]
    rows: list[dict] = []

    if preferred in xls.sheet_names:
        df_all = pd.read_excel(faq_path, sheet_name=preferred)
        rows = _rows_from_sheet(df_all)
        print(f"[ingest] Feuille '{preferred}': {len(rows)} lignes exploitables")

    if len(rows) < min_rows:
        print("[ingest] Feuille consolidee absente/incomplete -> consolidation par domaine")
        rows = []
        for sheet in xls.sheet_names:
            if sheet == preferred:
                continue
            df = pd.read_excel(faq_path, sheet_name=sheet)
            srows = _rows_from_sheet(df)
            rows.extend(srows)
            if srows:
                print(f"[ingest]   {sheet}: {len(srows)}")

    # Construction des enregistrements normalises
    records, seen = [], set()
    for rec in rows:
        variantes = [rec.get("variante_1"), rec.get("variante_2"), rec.get("variante_3")]
        variantes = [v for v in variantes if v]
        faq_id = _stable_id(rec)
        # desambiguation en cas de collision (rare)
        base_id = faq_id
        k = 1
        while faq_id in seen:
            faq_id = f"{base_id}-{k}"
            k += 1
        seen.add(faq_id)
        records.append({
            "faq_id": faq_id,
            "domaine": rec.get("domaine"),
            "motif": rec.get("motif"),
            "sous_motif": rec.get("sous_motif"),
            "question": rec.get("question"),
            "reponse": rec.get("reponse"),
            "variantes": variantes,
            "orientation": rec.get("orientation"),
            "view": rec.get("view"),
            "tags_ios": rec.get("tags_ios"),
            "tags_android": rec.get("tags_android"),
        })

    out_path = abspath(cfg["paths"]["faq_processed"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Meta d'index
    domains = {}
    for r in records:
        domains[r["domaine"]] = domains.get(r["domaine"], 0) + 1
    meta = {
        "source_file": faq_path.name,
        "n_records": len(records),
        "domains": domains,
    }
    meta_path = abspath(cfg["paths"]["index_meta"])
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)

    print(f"[ingest] OK -> {out_path} ({len(records)} lignes FAQ)")
    print(f"[ingest] Domaines: {domains}")
    return out_path


if __name__ == "__main__":
    ingest(sys.argv[1] if len(sys.argv) > 1 else None)
