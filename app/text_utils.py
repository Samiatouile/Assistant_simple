"""Normalisation de texte et dictionnaire de synonymes bancaires (FR / Darija).

Utilise par le retrieval ET les guardrails pour une detection robuste
(accents, apostrophes, fautes simples, darija/francais melange).
"""
from __future__ import annotations

import re
import unicodedata
from typing import List

# Dictionnaire de synonymes/darija -> terme canonique bancaire.
# Objectif: rapprocher les formulations darija/francais melange de la FAQ FR.
SYNONYMS = {
    "flous": "argent",
    "l3ard": "argent",
    "carta": "carte",
    "kart": "carte",
    "chhal 3andi": "solde",
    "chhal": "solde",
    "3andi": "solde",
    "tsift": "virement",
    "tsifit": "virement",
    "transfert": "virement",
    "khlass": "paiement",
    "khalass": "paiement",
    "nkhalas": "paiement",
    "tserqat": "vole",
    "sreqat": "vole",
    "msroq": "vole",
    "twedar": "perdu",
    "dork": "perdu",
    "hjar": "bloquer",
    "bloki": "bloquer",
    "blocage": "bloquer",
    "opposition": "bloquer",
    "compte bancaire": "compte",
    "hsab": "compte",
    "cin": "identite",
    "crédit": "credit",
    "credi": "credit",
    "salaf": "credit",
    "assurance": "assurance",
    "tamine": "assurance",
    "agence": "agence",
    "wakala": "agence",
}

# Termes canoniques utiles pour l'expansion (ajoutes en plus, pas remplaces).
EXPANSIONS = {
    "carte": ["carte bancaire"],
    "solde": ["compte"],
    "virement": ["transfert"],
}

_WORD_RE = re.compile(r"[a-z0-9]+")


def strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize(text: str) -> str:
    """Minuscule, sans accents, apostrophes/espaces normalises."""
    if text is None:
        return ""
    text = str(text).lower()
    text = strip_accents(text)
    # Apostrophes typographiques -> simple espace de separation
    text = text.replace("’", "'").replace("`", "'")
    text = re.sub(r"['’]", " ", text)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def tokens(text: str) -> List[str]:
    return _WORD_RE.findall(normalize(text))


def expand_synonyms(text: str) -> str:
    """Normalise puis remplace/etend les termes darija par leur canonique FR.

    Gere aussi les expressions multi-mots (ex: 'chhal 3andi' -> 'solde').
    """
    norm = normalize(text)
    # Expressions multi-mots d'abord
    for phrase, canonical in SYNONYMS.items():
        if " " in phrase:
            norm = norm.replace(normalize(phrase), canonical)
    out = []
    for tok in norm.split():
        canonical = SYNONYMS.get(tok, tok)
        out.append(canonical)
        for extra in EXPANSIONS.get(canonical, []):
            out.append(extra)
    return " ".join(out)


def token_overlap(a: str, b: str) -> float:
    """Recouvrement lexical normalise (Jaccard) apres expansion synonymes."""
    ta = set(expand_synonyms(a).split())
    tb = set(expand_synonyms(b).split())
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0
