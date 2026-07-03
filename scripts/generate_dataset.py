"""Generation du dataset de recette a partir de la FAQ officielle.

Produit:
  - data/test_dataset.xlsx        (cas derives automatiquement de la FAQ)
  - data/manual_test_cases.xlsx   (cas metier manuels: interdits/sensibles/securite...)

Pour chaque ligne FAQ on genere plusieurs formulations:
  - standard (Question type)
  - variantes (Variante 1..3)
  - imprecise (mots-cles)
  - avec faute simple
  - Darija (si une transformation simple est possible)

Le "Comportement attendu" est un ensemble de statuts acceptables separes par '|'
(ex: "ANSWER|ESCALATE|SENSITIVE") pour rester juste vis-a-vis des cas metier.
"""
from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import abspath, load_config  # noqa: E402
from app.response_builder import concise  # noqa: E402
from app.retrieval import load_faq  # noqa: E402
from app.text_utils import normalize  # noqa: E402

random.seed(42)

DATASET_COLUMNS = [
    "ID", "Domaine", "Motif de contact", "Sous-motif", "Type de test",
    "Intention", "Question", "Réponse attendue", "Comportement attendu",
    "Priorité", "Source FAQ ID", "Commentaire",
]

# Themes FAQ ou une reponse "sensible/escalade" est aussi acceptable.
_SENSITIVE_THEME = re.compile(
    r"fraud|vol|perte|opposition|securit|sécur|incident|litige|contestation|"
    r"compromis|phishing|blocage", re.IGNORECASE)

_STOPWORDS = {
    "quels", "quelles", "quel", "quelle", "sont", "les", "des", "une", "un",
    "pour", "que", "qui", "est", "ce", "de", "du", "la", "le", "en", "je",
    "puis", "comment", "ai", "mon", "ma", "mes", "avec", "dans", "sur", "il",
    "faut", "a", "au", "aux", "et", "ou", "par", "vous", "votre", "vos",
}

# canonique FR -> darija (pour transformation simple)
_DARIJA = {
    "carte": "carta", "virement": "tsift", "paiement": "khlass",
    "credit": "salaf", "crédit": "salaf", "agence": "wakala",
    "compte": "hsab", "argent": "flous", "assurance": "tamine",
}


def _expected_behavior(rec: dict) -> str:
    orientation = (rec.get("orientation") or "").strip().lower()
    theme = " ".join([rec.get("motif") or "", rec.get("sous_motif") or "",
                      rec.get("question") or ""])
    accept = ["ANSWER"]
    if orientation == "escalader":
        accept.append("ESCALATE")
    if _SENSITIVE_THEME.search(theme):
        accept += ["ESCALATE", "SENSITIVE"]
    # dedoublonne en gardant l'ordre
    seen, out = set(), []
    for s in accept:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return "|".join(out)


def _imprecise(question: str) -> str | None:
    toks = [t for t in normalize(question).split() if t not in _STOPWORDS and len(t) > 2]
    if len(toks) < 2:
        return None
    return " ".join(toks[:4])


def _typo(question: str) -> str | None:
    words = question.split()
    # cible le mot long
    idx = max(range(len(words)), key=lambda i: len(words[i])) if words else -1
    if idx < 0 or len(words[idx]) < 5:
        return None
    w = words[idx]
    # supprime un caractere au milieu (faute de frappe)
    cut = len(w) // 2
    words[idx] = w[:cut] + w[cut + 1:]
    return " ".join(words)


def _darija(question: str) -> str | None:
    norm = normalize(question)
    replaced = norm
    changed = False
    for fr, dar in _DARIJA.items():
        if re.search(rf"\b{re.escape(fr)}\b", replaced):
            replaced = re.sub(rf"\b{re.escape(fr)}\b", dar, replaced)
            changed = True
    return replaced if changed else None


def build_faq_dataset(faq: list[dict], cfg: dict) -> pd.DataFrame:
    max_words = cfg["app"]["max_answer_words"]
    rows = []
    for rec in faq:
        base = {
            "Domaine": rec.get("domaine"),
            "Motif de contact": rec.get("motif"),
            "Sous-motif": rec.get("sous_motif"),
            "Réponse attendue": concise(rec.get("reponse", ""), max_words),
            "Comportement attendu": _expected_behavior(rec),
            "Source FAQ ID": rec["faq_id"],
            "Intention": rec.get("sous_motif") or rec.get("motif"),
        }
        q = rec.get("question", "")

        def add(suffix, ttype, question, prio, comment):
            if not question:
                return
            rows.append({
                **base, "ID": f"{rec['faq_id']}-{suffix}", "Type de test": ttype,
                "Question": question, "Priorité": prio, "Commentaire": comment,
            })

        add("STD", "FAQ", q, "P1", "Formulation standard (Question type)")
        for i, v in enumerate(rec.get("variantes", []), start=1):
            add(f"V{i}", "Variante", v, "P1", f"Variante {i}")
        add("IMP", "Variante", _imprecise(q), "P2", "Formulation imprecise (mots-cles)")
        add("TYP", "Variante", _typo(q), "P2", "Formulation avec faute simple")
        add("DAR", "Variante", _darija(q), "P2", "Formulation Darija (transformation simple)")

    df = pd.DataFrame(rows)
    return df.reindex(columns=DATASET_COLUMNS)


def build_manual_dataset() -> pd.DataFrame:
    seed_path = abspath("data/manual_test_cases_seed.json")
    with open(seed_path, "r", encoding="utf-8") as fh:
        cases = json.load(fh)
    rows = []
    for i, c in enumerate(cases, start=1):
        rows.append({
            "ID": f"MAN-{i:04d}",
            "Domaine": c.get("Domaine", ""),
            "Motif de contact": c.get("Motif de contact", ""),
            "Sous-motif": c.get("Sous-motif", ""),
            "Type de test": c["Type de test"],
            "Intention": c.get("Intention", ""),
            "Question": c["Question"],
            "Réponse attendue": c.get("Réponse attendue", ""),
            "Comportement attendu": c["Comportement attendu"],
            "Priorité": c.get("Priorite", "P1"),
            "Source FAQ ID": c.get("Source FAQ ID", ""),
            "Commentaire": c.get("Commentaire", ""),
            "Conversation": c.get("Conversation", ""),
            "Ordre": c.get("Ordre", ""),
        })
    return pd.DataFrame(rows)


def main() -> None:
    cfg = load_config()
    faq = load_faq()
    df_faq = build_faq_dataset(faq, cfg)
    df_manual = build_manual_dataset()

    out_faq = abspath("data/test_dataset.xlsx")
    out_manual = abspath("data/manual_test_cases.xlsx")
    df_faq.to_excel(out_faq, index=False)
    df_manual.to_excel(out_manual, index=False)

    print(f"[dataset] FAQ    -> {out_faq}  ({len(df_faq)} cas)")
    print(f"[dataset] Manuel -> {out_manual}  ({len(df_manual)} cas)")
    print("[dataset] Repartition par type de test:")
    print(df_faq["Type de test"].value_counts().to_string())


if __name__ == "__main__":
    main()
