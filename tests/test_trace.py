"""Tests de tracabilite et de masquage des secrets."""
from app.trace import _mask, _mask_deep, new_trace_id


def test_trace_id_is_uuid_like():
    tid = new_trace_id()
    assert len(tid) >= 32


def test_mask_card_number():
    masked = _mask("ma carte 4111 1111 1111 1111 est bloquee")
    assert "4111" not in masked
    assert "MASQUE" in masked


def test_mask_otp_and_pin():
    assert "123456" not in _mask("mon otp est 123456")
    assert "MASQUE" in _mask("code pin: 4321 aa")


def test_mask_long_digit_runs():
    masked = _mask("mon rib est 12345678901234")
    assert "12345678901234" not in masked


def test_mask_deep_nested():
    obj = {"q": "otp 999999", "list": ["carte 4111111111111111"]}
    out = _mask_deep(obj, 6)
    assert "999999" not in out["q"]
    assert "4111111111111111" not in out["list"][0]
