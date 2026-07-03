"""Tests du moteur de recherche hybride."""


def test_exact_question_top_match(index):
    cands = index.search("Comment ouvrir un compte Simple ?")
    assert cands[0].faq_id == "FAQ-t001"
    assert cands[0].score > 0.5


def test_variant_matches_same_faq(index):
    cands = index.search("De quels papiers ai-je besoin pour ouvrir un compte Simple ?")
    assert cands[0].faq_id == "FAQ-t002"


def test_typo_still_matches(index):
    # faute simple: "reclamation" -> "reclmation"
    cands = index.search("Comment deposer une reclmation ?")
    assert cands[0].faq_id == "FAQ-t005"


def test_darija_synonym_matches(index):
    # "carta" (darija) doit rapprocher de la carte
    cands = index.search("activer carta")
    assert cands[0].faq_id == "FAQ-t003"


def test_out_of_domain_low_score(index):
    cands = index.search("Quelle est la meteo a Tokyo demain ?")
    assert cands[0].score < 0.4


def test_returns_top_k(index):
    cands = index.search("virement", top_k=3)
    assert len(cands) <= 3
