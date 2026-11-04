"""Text normalization helpers used when loading data and searching."""

from __future__ import annotations

import re
import unicodedata


def normalize_text(value: str | None) -> str:
    """Lowercase, accent-free, single-spaced text for forgiving searches.

    >>> normalize_text("  Violão  Clássico ")
    'violao classico'
    """
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", without_accents.lower()).strip()


def only_digits(value: str | None) -> str:
    """Keep digits only, to match phone numbers typed in different formats.

    >>> only_digits("(67) 99812-3456")
    '67998123456'
    """
    return re.sub(r"\D", "", value or "")

