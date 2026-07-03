"""Construction de la reponse finale a partir de la 'Reponse officielle'.

- Mode par defaut (deterministe): version propre et concise de la reponse FAQ,
  sans aucun ajout metier.
- Mode LLM (optionnel via .env): reformulation courte, bornee a la source, avec
  verification post-generation. En cas de doute -> reponse officielle brute.

Le LLM n'a JAMAIS le droit de repondre sans extrait FAQ.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional

from app.config import abspath, get_env, load_config
from app.text_utils import token_overlap

PROMPT_VERSION = "1.0.0"


def _read_prompt(name: str) -> str:
    p = abspath(f"prompts/{name}")
    try:
        return Path(p).read_text(encoding="utf-8")
    except Exception:
        return ""


def _split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def concise(text: str, max_words: int) -> str:
    """Version concise: nettoie et tronque proprement a max_words environ."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    words = text.split()
    if len(words) <= max_words:
        return text
    # Tronque a la derniere phrase complete sous la limite
    out, count = [], 0
    for sent in _split_sentences(text):
        w = sent.split()
        if count + len(w) > max_words and out:
            break
        out.append(sent)
        count += len(w)
    result = " ".join(out) if out else " ".join(words[:max_words])
    return result.strip()


def deterministic_answer(reponse_officielle: str, cfg: Optional[dict] = None) -> str:
    cfg = cfg or load_config()
    return concise(reponse_officielle, cfg["app"]["max_answer_words"])


# --- LLM optionnel -----------------------------------------------------------
def _llm_generate(question: str, extraits: List[str], cfg: dict) -> Optional[str]:
    """Appelle le LLM configure. Retourne None si indisponible/erreur."""
    provider = cfg["llm"]["provider"]
    system = _read_prompt("system_prompt.md")
    guide = _read_prompt("response_prompt.md")
    context = "\n\n".join(f"[Extrait FAQ]\n{e}" for e in extraits)
    user = (
        f"{guide}\n\nQuestion: {question}\n\nExtraits FAQ (source unique):\n{context}"
    )
    try:
        if provider == "openai":
            from openai import OpenAI

            client = OpenAI(api_key=get_env("OPENAI_API_KEY"))
            resp = client.chat.completions.create(
                model=get_env("OPENAI_MODEL", "gpt-4o-mini"),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=cfg["llm"]["temperature"],
                max_tokens=cfg["llm"]["max_tokens"],
            )
            return resp.choices[0].message.content.strip()
        if provider == "anthropic":
            import anthropic

            client = anthropic.Anthropic(api_key=get_env("ANTHROPIC_API_KEY"))
            resp = client.messages.create(
                model=get_env("ANTHROPIC_MODEL", "claude-haiku-4-5"),
                system=system,
                max_tokens=cfg["llm"]["max_tokens"],
                temperature=cfg["llm"]["temperature"],
                messages=[{"role": "user", "content": user}],
            )
            return "".join(b.text for b in resp.content if b.type == "text").strip()
    except Exception as exc:  # pragma: no cover - depend du reseau/cle
        print(f"[response_builder] LLM indisponible ({exc}); fallback deterministe.")
        return None
    return None


def build_answer(question: str, reponse_officielle: str,
                 extra_reponses: Optional[List[str]] = None,
                 cfg: Optional[dict] = None) -> dict:
    """Construit la reponse finale et indique le mode utilise.

    Retourne {answer, mode, source_overlap}.
    """
    cfg = cfg or load_config()
    extraits = [reponse_officielle] + list(extra_reponses or [])
    source_blob = " ".join(extraits)

    if cfg["llm"].get("enabled") and cfg["llm"].get("provider") not in (None, "none"):
        generated = _llm_generate(question, extraits, cfg)
        if generated:
            overlap = token_overlap(generated, source_blob)
            if overlap >= cfg["llm"]["min_source_overlap"] and len(generated.split()) <= cfg["app"]["max_answer_words"] * 1.3:
                return {"answer": concise(generated, cfg["app"]["max_answer_words"]),
                        "mode": "llm", "source_overlap": round(overlap, 3)}
            # Non aligne -> fallback sur la reponse officielle brute
            return {"answer": deterministic_answer(reponse_officielle, cfg),
                    "mode": "llm_fallback", "source_overlap": round(overlap, 3)}

    return {"answer": deterministic_answer(reponse_officielle, cfg),
            "mode": "deterministic", "source_overlap": 1.0}
