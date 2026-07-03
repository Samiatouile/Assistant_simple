"""Orchestration de l'assistant: guardrails -> retrieval -> decision -> reponse.

Priorite du comportement (contrainte metier):
  1. Securite  2. Refus hors perimetre  3. Non-hallucination
  4. Tracabilite  5. Reponse FAQ correcte  6. Concision mobile
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.config import load_config
from app.guardrails import check_output_leak, run_input_guardrails
from app.response_builder import PROMPT_VERSION, build_answer
from app.retrieval import Candidate, RetrievalIndex, get_index
from app.text_utils import tokens
from app.trace import new_trace_id, now_iso, write_trace

# Connecteurs qui marquent une CONTINUATION (ellipse) du sujet precedent.
_FOLLOWUP_STARTERS = {"et", "oui", "non", "ok", "aussi", "donc", "alors", "puis",
                      "sinon", "ca", "cela", "meme", "ceux"}
# Mots interrogatifs: une question qui commence par la est AUTONOME (nouveau
# sujet), jamais une simple continuation -> pas d'augmentation contextuelle.
_INTERROGATIVES = {"comment", "quel", "quelle", "quels", "quelles", "ou",
                   "pourquoi", "quand", "combien", "qui", "quoi", "est",
                   "peut", "puis", "y"}


@dataclass
class SessionContext:
    last_domain: Optional[str] = None
    last_motif: Optional[str] = None
    last_faq_id: Optional[str] = None
    last_question: Optional[str] = None
    last_orientation: Optional[str] = None
    history: deque = field(default_factory=lambda: deque(maxlen=6))


class SessionStore:
    def __init__(self) -> None:
        self._sessions: Dict[str, SessionContext] = {}

    def get(self, session_id: str) -> SessionContext:
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionContext()
        return self._sessions[session_id]

    def reset(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


class Assistant:
    def __init__(self, index: Optional[RetrievalIndex] = None,
                 store: Optional[SessionStore] = None):
        self.cfg = load_config()
        self.index = index or get_index()
        self.store = store or SessionStore()

    # -- contexte conversationnel --------------------------------------------
    def _is_followup(self, query: str, ctx: SessionContext) -> bool:
        """Vrai suivi = continuation elliptique, PAS une nouvelle question.

        - commence par un interrogatif (comment/quel/ou...) -> question autonome
        - commence par un connecteur (et/oui/donc...) -> continuation
        - fragment tres court sans interrogatif -> continuation
        """
        toks = tokens(query)
        if not ctx.last_question or not toks:
            return False
        first = toks[0]
        if first in _INTERROGATIVES:
            return False  # question autonome -> ne pas detourner vers l'ancien sujet
        if first in _FOLLOWUP_STARTERS:
            return True
        # fragment court (ex: "les documents ?") sans interrogatif ni verbe clair
        return len(toks) <= self.cfg["conversation"]["followup_max_tokens"]

    def _augment(self, query: str, ctx: SessionContext) -> str:
        parts = [query]
        if ctx.last_question:
            parts.append(ctx.last_question)
        if ctx.last_motif:
            parts.append(ctx.last_motif)
        return " ".join(parts)

    # -- construction de la reponse standard ---------------------------------
    def _base_response(self, trace_id: str) -> Dict[str, Any]:
        return {
            "trace_id": trace_id,
            "answer": "",
            "status": "",
            "confidence": 0.0,
            "domain": None,
            "motif": None,
            "sous_motif": None,
            "orientation": None,
            "matched_faq_ids": [],
            "retrieval_scores": [],
            "sources": [],
            "guardrail_flags": [],
            "debug_reason": "",
        }

    def _navigation_hint(self, cand: Candidate) -> Optional[str]:
        if cand.view and str(cand.view).lower() != "nan":
            return f"Dans l'application Simple: {cand.view}."
        return None

    def answer(self, question: str, session_id: str = "default",
               debug: bool = False) -> Dict[str, Any]:
        t0 = time.time()
        trace_id = new_trace_id()
        ctx = self.store.get(session_id)
        resp = self._base_response(trace_id)
        thr = self.cfg["thresholds"]

        # 1) Garde-fous d'entree (securite, interdits, sensibles)
        guard = run_input_guardrails(question)
        candidates: List[Candidate] = []
        used_query = question
        followup = False

        if guard.triggered:
            resp.update({
                "answer": guard.message,
                "status": guard.status,
                "orientation": guard.orientation,
                "guardrail_flags": guard.flags,
                "debug_reason": guard.debug_reason,
            })
        else:
            # 2) Retrieval sur la question brute d'abord.
            candidates = self.index.search(question, top_k=self.cfg["retrieval"]["top_k"])
            # Suivi conversationnel UNIQUEMENT si la question brute est faible
            # (evite de detourner une question courte mais autonome).
            top_raw_score = candidates[0].score if candidates else 0.0
            if top_raw_score < thr["answer"] and self._is_followup(question, ctx):
                aug = self._augment(question, ctx)
                cand_aug = self.index.search(aug, top_k=self.cfg["retrieval"]["top_k"])
                # On n'accepte le contexte que s'il RESTE dans le domaine du
                # dernier echange (continuite de sujet) ET ameliore le score.
                # Evite de ressortir la reponse d'un sujet precedent sans rapport.
                if (cand_aug and cand_aug[0].score > top_raw_score
                        and cand_aug[0].score >= thr["answer"]
                        and cand_aug[0].domaine == ctx.last_domain):
                    candidates = cand_aug
                    used_query = aug
                    followup = True

            resp["retrieval_scores"] = [round(c.score, 4) for c in candidates]
            resp["sources"] = [
                {"faq_id": c.faq_id, "domaine": c.domaine, "question": c.question}
                for c in candidates
            ]

            top = candidates[0] if candidates else None
            second = candidates[1] if len(candidates) > 1 else None

            if top is None or top.score < thr["out_of_scope"]:
                resp.update({
                    "status": "OUT_OF_SCOPE",
                    "answer": (
                        "Je ne dispose pas de cette information dans mon perimetre. "
                        "Pour une reponse precise, contactez le service client Simple "
                        "ou votre agence."
                    ),
                    "orientation": "Service client / Agence",
                    "confidence": round(top.score, 4) if top else 0.0,
                    "debug_reason": "score < seuil out_of_scope (hors connaissance)",
                })
            elif top.score < thr["answer"] or (
                top.score < thr["answer"] + 0.12  # pas de match clairement dominant
                and second and (top.score - second.score) < thr["clarify_margin"]
                and second.score >= thr["clarify_min"]
                and second.domaine != top.domaine
            ):
                # 3a) Ambigu -> demande de clarification
                options = []
                for c in candidates[:3]:
                    if c.score >= thr["clarify_min"]:
                        options.append(f"- {c.question} ({c.domaine})")
                resp.update({
                    "status": "CLARIFY",
                    "answer": (
                        "Pouvez-vous preciser votre demande ? Voici des sujets proches:\n"
                        + "\n".join(options)
                    ),
                    "domain": top.domaine,
                    "motif": top.motif,
                    "confidence": round(top.score, 4),
                    "matched_faq_ids": [c.faq_id for c in candidates[:3] if c.score >= thr["clarify_min"]],
                    "orientation": "Clarification",
                    "debug_reason": "score intermediaire ou candidats proches multi-domaines",
                })
                # Memoriser le sujet propose pour qu'une confirmation courte
                # ("oui, le credit conso") reste dans le bon domaine.
                ctx.last_domain = top.domaine
                ctx.last_motif = top.motif
                ctx.last_question = top.question
                ctx.last_orientation = "Clarification"
            else:
                # 3b) Reponse FAQ (ANSWER ou ESCALATE selon orientation FAQ)
                built = build_answer(question, top.reponse, cfg=self.cfg)
                answer_text = built["answer"]
                status = "ANSWER"
                orientation = top.orientation
                if str(top.orientation).strip().lower() == "escalader":
                    status = "ESCALATE"
                    answer_text += (
                        "\n\nPour finaliser, un conseiller peut vous accompagner: "
                        "contactez le service client Simple ou votre agence."
                    )
                nav = self._navigation_hint(top)
                if nav and status == "ANSWER":
                    answer_text += "\n" + nav

                # Garde-fou de sortie anti-fuite
                flags = []
                if check_output_leak(answer_text):
                    answer_text = (
                        "Je ne peux pas traiter cette demande. Posez-moi une question "
                        "couverte par la FAQ officielle."
                    )
                    status = "REFUSE"
                    flags.append("SECURITY_OUTPUT_LEAK")

                resp.update({
                    "answer": answer_text,
                    "status": status,
                    "domain": top.domaine,
                    "motif": top.motif,
                    "sous_motif": top.sous_motif,
                    "orientation": orientation,
                    "confidence": round(min(max(top.score, 0.0), 1.0), 4),
                    "matched_faq_ids": [top.faq_id],
                    "guardrail_flags": flags,
                    "debug_reason": f"match FAQ (champ={top.matched_field}, mode={built['mode']})"
                    + (" [suivi conversationnel]" if followup else ""),
                })
                # Mise a jour du contexte conversationnel
                ctx.last_domain = top.domaine
                ctx.last_motif = top.motif
                ctx.last_faq_id = top.faq_id
                ctx.last_question = top.question
                ctx.last_orientation = orientation

        # Historique de session
        ctx.history.append({"q": question, "status": resp["status"]})

        # 4) Trace / observabilite
        elapsed_ms = int((time.time() - t0) * 1000)
        trace = {
            "trace_id": trace_id,
            "timestamp": now_iso(),
            "session_id": session_id,
            "question": question,
            "question_normalisee": used_query,
            "followup": followup,
            "status": resp["status"],
            "candidates": [c.to_dict() for c in candidates],
            "retrieval_scores": resp["retrieval_scores"],
            "selected_faq_ids": resp["matched_faq_ids"],
            "reponse_officielle": candidates[0].reponse if (candidates and resp["matched_faq_ids"]) else None,
            "answer": resp["answer"],
            "guardrail_flags": resp["guardrail_flags"],
            "confidence": resp["confidence"],
            "orientation": resp["orientation"],
            "duration_ms": elapsed_ms,
            "index_version": self.index.version(),
            "prompt_version": PROMPT_VERSION,
            "debug_reason": resp["debug_reason"],
        }
        write_trace(trace)

        resp["processing_ms"] = elapsed_ms
        resp["index_version"] = self.index.version()
        if debug:
            resp["debug"] = {
                "candidates": [c.to_dict() for c in candidates],
                "question_normalisee": used_query,
                "followup": followup,
            }
        return resp


# Singleton d'assistant (charge l'index une fois)
_ASSISTANT: Optional[Assistant] = None


def get_assistant() -> Assistant:
    global _ASSISTANT
    if _ASSISTANT is None:
        _ASSISTANT = Assistant()
    return _ASSISTANT
