"""Construit (et verifie) l'index de retrieval a partir de data/processed/faq.jsonl.

L'index lexical est reconstruit en memoire au demarrage de l'API; ce script
sert a valider l'ingestion et a precharger les embeddings si actives.

Usage:
    python scripts/build_index.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.retrieval import get_index  # noqa: E402


def main() -> None:
    index = get_index(force_reload=True)
    print(f"[build_index] Lignes FAQ indexees : {len(index.records)}")
    print(f"[build_index] Version index       : {index.version()}")
    print(f"[build_index] Embeddings actifs    : {index.embeddings_active}")
    # Petit test de sante
    demo = index.search("comment ouvrir un compte", top_k=3)
    print("[build_index] Test 'comment ouvrir un compte':")
    for c in demo:
        print(f"   {c.score:.3f}  [{c.domaine}] {c.question[:70]}")


if __name__ == "__main__":
    main()
