"""Fixtures pytest: assistant charge sur un mini-FAQ de test (non confidentiel).

Les tests n'utilisent PAS la vraie FAQ: ils s'appuient sur tests/fixtures/
sample_faq.jsonl pour etre reproductibles partout (CI incluse).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.chatbot import Assistant, SessionStore  # noqa: E402
from app.retrieval import RetrievalIndex, load_faq  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "sample_faq.jsonl"


@pytest.fixture(scope="session")
def fixture_records():
    return load_faq(str(FIXTURE))


@pytest.fixture(scope="session")
def index(fixture_records):
    return RetrievalIndex(fixture_records)


@pytest.fixture()
def assistant(index):
    # Nouvelle store a chaque test pour eviter le partage de contexte
    return Assistant(index=index, store=SessionStore())
