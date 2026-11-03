"""Utilitários de normalização de texto usados na carga e nas buscas."""

from __future__ import annotations

import re
import unicodedata


def normalize_text(value: str | None) -> str:
    """Minúsculas, sem acentos e com espaços simples — para buscas tolerantes.

    >>> normalize_text("  Violão  Clássico ")
    'violao classico'
    """
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", without_accents.lower()).strip()


def only_digits(value: str | None) -> str:
    """Mantém apenas dígitos — usado para comparar telefones em formatos diferentes.

    >>> only_digits("(67) 99812-3456")
    '67998123456'
    """
    return re.sub(r"\D", "", value or "")

