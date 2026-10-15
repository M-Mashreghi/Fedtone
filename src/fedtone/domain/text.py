"""Conservative text normalization and content hashing.

Normalization must never change policy wording: it only applies Unicode NFC, turns
non-breaking and other Unicode spaces into ordinary spaces, collapses whitespace runs inside
a paragraph and drops empty paragraphs. Punctuation, quotes, hyphens, numbers and paragraph
order are preserved. Raw content is kept separately for audit.
"""

import hashlib
import re
import unicodedata
from collections.abc import Iterable

NORMALIZER_VERSION = "conservative-1"

_WHITESPACE = re.compile(r"\s+")
# Zero-width characters carry no wording but break exact matching and diffs.
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"))


def normalize_paragraph(paragraph: str) -> str:
    text = unicodedata.normalize("NFC", paragraph).translate(_ZERO_WIDTH)
    # In Python regexes, \s already covers NBSP and the other Unicode space separators.
    return _WHITESPACE.sub(" ", text).strip()


def normalize_paragraphs(paragraphs: Iterable[str]) -> tuple[str, ...]:
    normalized = (normalize_paragraph(p) for p in paragraphs)
    return tuple(p for p in normalized if p)


def join_paragraphs(paragraphs: Iterable[str]) -> str:
    return "\n\n".join(paragraphs)


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()
