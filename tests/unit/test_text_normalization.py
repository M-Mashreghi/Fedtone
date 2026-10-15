import pytest

from fedtone.domain.text import (
    join_paragraphs,
    normalize_paragraph,
    normalize_paragraphs,
    sha256_hex,
)


def test_collapses_whitespace_and_nbsp_without_changing_words() -> None:
    raw = "  inflation at the rate of 2 percent\n   over the longer run.  "

    assert normalize_paragraph(raw) == "inflation at the rate of 2 percent over the longer run."


def test_preserves_punctuation_numbers_and_quotes() -> None:
    text = "range at 5-1/4 to 5-1/2 percent; the Committee's “greater confidence”—noted."

    assert normalize_paragraph(text) == text


def test_applies_nfc_and_removes_zero_width_characters() -> None:
    decomposed = "Président​ Lagarde"

    assert normalize_paragraph(decomposed) == "Président Lagarde"


def test_drops_empty_paragraphs_and_keeps_order() -> None:
    assert normalize_paragraphs(["B first", " ", "", "A second "]) == ("B first", "A second")


def test_normalization_is_idempotent() -> None:
    once = normalize_paragraphs(["  a  b ", "c\n d"])

    assert normalize_paragraphs(once) == once


@pytest.mark.parametrize("data", [b"abc", "abc"])
def test_sha256_is_deterministic(data) -> None:
    expected = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"

    assert sha256_hex(data) == expected
    assert sha256_hex(join_paragraphs(["a", "b"])) == sha256_hex("a\n\nb")
