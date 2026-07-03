"""API FastAPI de l'Assistant Virtuel Simple (MVP).

Endpoints:
  GET  /health         -> etat + version index
  POST /chat           -> reponse assistant (schema de recette complet)
  POST /session/reset  -> nouvelle session
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

from app.chatbot import get_assistant

app = FastAPI(title="Assistant Virtuel Simple - MVP", version="0.1.0")


class ChatRequest(BaseModel):
    message: str = Field(..., description="Question de l'utilisateur")
    session_id: str = Field("default", description="Identifiant de session")
    debug: bool = Field(False, description="Inclure les infos de recette/debug")


class ChatResponse(BaseModel):
    trace_id: str
    answer: str
    status: str
    confidence: float
    domain: Optional[str] = None
    motif: Optional[str] = None
    sous_motif: Optional[str] = None
    orientation: Optional[str] = None
    matched_faq_ids: List[str] = []
    retrieval_scores: List[float] = []
    sources: List[Dict[str, Any]] = []
    guardrail_flags: List[str] = []
    debug_reason: str = ""
    processing_ms: Optional[int] = None
    index_version: Optional[str] = None
    debug: Optional[Dict[str, Any]] = None


@app.get("/health")
def health() -> Dict[str, Any]:
    a = get_assistant()
    return {
        "status": "ok",
        "n_faq": len(a.index.records),
        "index_version": a.index.version(),
        "embeddings": a.index.embeddings_active,
    }


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> Dict[str, Any]:
    a = get_assistant()
    return a.answer(req.message, session_id=req.session_id, debug=req.debug)


class ResetRequest(BaseModel):
    session_id: str = "default"


@app.post("/session/reset")
def reset(req: ResetRequest) -> Dict[str, str]:
    get_assistant().store.reset(req.session_id)
    return {"status": "reset", "session_id": req.session_id}


if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run(
        "app.api:app",
        host=os.getenv("API_HOST", "127.0.0.1"),
        port=int(os.getenv("API_PORT", "8000")),
        reload=False,
    )
