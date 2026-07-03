"""Moteur de recherche hybride sur la FAQ (source unique de verite).

- Normalisation (accents, apostrophes, fautes) + dictionnaire synonymes darija.
- Indexation par sous-documents (Question type + Variantes) pour bien matcher
  les reformulations.
- Fusion: TF-IDF mots (1-2 grams) + TF-IDF char n-grams + recouvrement lexical.
- Embeddings multilingues optionnels (sentence-transformers) si actives.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.config import abspath, load_config
from app.text_utils import expand_synonyms


@dataclass
class Candidate:
    faq_id: str
    score: float
    domaine: str
    motif: str
    sous_motif: str
    question: str
    reponse: str
    orientation: str
    view: Optional[str] = None
    tags_ios: Optional[str] = None
    tags_android: Optional[str] = None
    matched_field: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "faq_id": self.faq_id,
            "score": round(float(self.score), 4),
            "domaine": self.domaine,
            "motif": self.motif,
            "sous_motif": self.sous_motif,
            "question": self.question,
            "orientation": self.orientation,
            "matched_field": self.matched_field,
        }


def load_faq(path: Optional[str] = None) -> List[Dict[str, Any]]:
    cfg = load_config()
    fpath = Path(path) if path else abspath(cfg["paths"]["faq_processed"])
    if not fpath.exists():
        raise FileNotFoundError(
            f"FAQ indexee introuvable: {fpath}. Lancez d'abord "
            f"`python scripts/ingest_faq.py`."
        )
    records = []
    with open(fpath, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


class RetrievalIndex:
    """Index en memoire construit a partir de la FAQ processed."""

    def __init__(self, records: List[Dict[str, Any]], cfg: Optional[dict] = None):
        self.cfg = cfg or load_config()
        self.records = records
        self.by_id = {r["faq_id"]: r for r in records}
        rcfg = self.cfg["retrieval"]

        # Construction des sous-documents (chaque variante -> ligne FAQ).
        self._sub_texts: List[str] = []
        self._sub_faq_ids: List[str] = []
        self._sub_fields: List[str] = []
        for r in records:
            fields = [("question", r.get("question", ""))]
            for i, v in enumerate(r.get("variantes", []), start=1):
                if v:
                    fields.append((f"variante_{i}", v))
            # petit contexte metier pour ancrer le domaine
            ctx = f"{r.get('motif','')} {r.get('sous_motif','')}"
            for name, txt in fields:
                combined = f"{txt} {ctx}"
                self._sub_texts.append(expand_synonyms(combined))
                self._sub_faq_ids.append(r["faq_id"])
                self._sub_fields.append(name)
        # texte brut (pour token_overlap, sans le contexte pour rester precis)
        self._sub_raw: List[str] = []
        for r in records:
            fields = [r.get("question", "")] + [v for v in r.get("variantes", []) if v]
            for f in fields:
                self._sub_raw.append(f)
        # Pre-calcul des ensembles de tokens (expansion synonymes) pour un
        # recouvrement lexical rapide au moment de la recherche.
        self._sub_token_sets = [set(expand_synonyms(t).split()) for t in self._sub_raw]

        # Vectoriseurs lexicaux
        self._word_vec = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=1)
        self._char_vec = TfidfVectorizer(
            analyzer="char_wb",
            ngram_range=(rcfg["char_ngram_min"], rcfg["char_ngram_max"]),
            min_df=1,
        )
        self._word_mat = self._word_vec.fit_transform(self._sub_texts)
        self._char_mat = self._char_vec.fit_transform(self._sub_texts)

        # Embeddings optionnels
        self._emb_model = None
        self._emb_mat = None
        if rcfg.get("use_embeddings"):
            self._try_load_embeddings()

    def _try_load_embeddings(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer

            model_name = self.cfg["retrieval"]["embedding_model"]
            self._emb_model = SentenceTransformer(model_name)
            self._emb_mat = self._emb_model.encode(
                self._sub_texts, normalize_embeddings=True, show_progress_bar=False
            )
        except Exception as exc:  # pragma: no cover - depend de l'install
            print(f"[retrieval] embeddings indisponibles ({exc}); fallback lexical.")
            self._emb_model = None
            self._emb_mat = None

    @property
    def embeddings_active(self) -> bool:
        return self._emb_mat is not None

    def version(self) -> str:
        emb = "emb" if self.embeddings_active else "lex"
        return f"idx-{len(self.records)}-{emb}-v1"

    def search(self, query: str, top_k: Optional[int] = None) -> List[Candidate]:
        rcfg = self.cfg["retrieval"]
        top_k = top_k or rcfg["top_k"]
        q_expanded = expand_synonyms(query)

        q_word = self._word_vec.transform([q_expanded])
        q_char = self._char_vec.transform([q_expanded])
        word_sim = cosine_similarity(q_word, self._word_mat)[0]
        char_sim = cosine_similarity(q_char, self._char_mat)[0]

        w_word = rcfg["weight_word_tfidf"]
        w_char = rcfg["weight_char_tfidf"]
        w_over = rcfg["weight_token_overlap"]

        emb_sim = None
        if self.embeddings_active:
            q_emb = self._emb_model.encode([q_expanded], normalize_embeddings=True)
            emb_sim = cosine_similarity(q_emb, self._emb_mat)[0]
            w_emb = rcfg["weight_embeddings"]
            # renormalisation des poids lexicaux pour laisser place aux embeddings
            total = w_word + w_char + w_over + w_emb
            w_word, w_char, w_over, w_emb = (
                w_word / total, w_char / total, w_over / total, w_emb / total,
            )

        # Recouvrement lexical rapide (ensembles de tokens pre-calcules)
        q_set = set(expand_synonyms(query).split())
        overlaps = np.zeros(len(self._sub_token_sets))
        if q_set:
            for i, s in enumerate(self._sub_token_sets):
                if s:
                    inter = len(q_set & s)
                    if inter:
                        overlaps[i] = inter / len(q_set | s)

        # Score par sous-document (vectorise)
        sub_scores = w_word * word_sim + w_char * char_sim + w_over * overlaps
        if emb_sim is not None:
            sub_scores = sub_scores + w_emb * emb_sim

        # Agregation max par ligne FAQ
        best: Dict[str, tuple] = {}
        for i, faq_id in enumerate(self._sub_faq_ids):
            if faq_id not in best or sub_scores[i] > best[faq_id][0]:
                best[faq_id] = (sub_scores[i], self._sub_fields[i])

        ranked = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)[:top_k]
        candidates: List[Candidate] = []
        for faq_id, (score, field_name) in ranked:
            r = self.by_id[faq_id]
            candidates.append(
                Candidate(
                    faq_id=faq_id,
                    score=float(score),
                    domaine=r.get("domaine", ""),
                    motif=r.get("motif", ""),
                    sous_motif=r.get("sous_motif", ""),
                    question=r.get("question", ""),
                    reponse=r.get("reponse", ""),
                    orientation=r.get("orientation", ""),
                    view=r.get("view"),
                    tags_ios=r.get("tags_ios"),
                    tags_android=r.get("tags_android"),
                    matched_field=field_name,
                )
            )
        return candidates


# --- Singleton pratique ------------------------------------------------------
_INDEX: Optional[RetrievalIndex] = None


def get_index(force_reload: bool = False, path: Optional[str] = None) -> RetrievalIndex:
    global _INDEX
    if _INDEX is None or force_reload:
        _INDEX = RetrievalIndex(load_faq(path))
    return _INDEX
