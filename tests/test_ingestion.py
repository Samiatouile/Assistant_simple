"""Tests d'ingestion / index de la FAQ."""
from app.retrieval import RetrievalIndex, load_faq
from tests.conftest import FIXTURE


def test_faq_loaded(fixture_records):
    assert len(fixture_records) == 10
    for rec in fixture_records:
        assert rec["faq_id"]
        assert rec["question"]
        assert rec["reponse"]


def test_all_records_have_metadata(fixture_records):
    for rec in fixture_records:
        assert "domaine" in rec
        assert "orientation" in rec
        assert isinstance(rec.get("variantes"), list)


def test_index_builds_and_versions(index):
    assert len(index.records) == 10
    assert index.version().startswith("idx-10-")


def test_ids_are_unique(fixture_records):
    ids = [r["faq_id"] for r in fixture_records]
    assert len(ids) == len(set(ids))


def test_load_faq_reads_fixture():
    recs = load_faq(str(FIXTURE))
    assert recs and all(r.get("reponse") for r in recs)
