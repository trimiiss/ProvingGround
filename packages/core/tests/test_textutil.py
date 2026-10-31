from __future__ import annotations

import pytest

from provingground import textutil


@pytest.mark.parametrize(
    ("digits", "valid"),
    [("4111111111111111", True), ("4111 1111 1111 1111", True), ("1234567812345678", False), ("4111", False)],
)
def test_luhn(digits: str, valid: bool) -> None:
    assert textutil.luhn_valid(digits) is valid


def test_find_pii_requires_valid_card() -> None:
    found = textutil.find_pii("Reach jane@example.com or (312) 555-0134; card 4111 1111 1111 1111.")
    assert found["email"] == ["jane@example.com"]
    assert found["phone"]
    assert found["card"]
    # An invalid card number must not be reported.
    assert "card" not in textutil.find_pii("order number 1234 5678 9012 3456 shipped")


def test_refusal_vs_abstention() -> None:
    assert textutil.is_refusal("I'm sorry, but I can't help with that request.")
    assert not textutil.is_refusal("Footwear can be returned within 30 days.")
    assert textutil.is_abstention("I don't have that information in the documentation.")
    # An abstention is not a policy refusal.
    assert not textutil.is_refusal("I don't have information about that in the documents.")


def test_numbers_and_token_f1() -> None:
    assert set(textutil.numbers("It costs $6.95 and ships in 5 to 7 days (30%).")) == {"6.95", "5", "7", "30"}
    assert textutil.token_f1("the quick brown fox", "the quick brown fox") == 1.0
    assert textutil.token_f1("completely different words here", "unrelated other tokens") == 0.0
