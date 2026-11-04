"""Parses the store's Policies and Procedures Manual (PDF) into searchable sections.

Why sections instead of vector RAG?
  * The manual is small (~15k characters, 10 numbered sections) and already organized
    by subject. Splitting on its own numbering (1., 4.1, ...) keeps each rule's full
    context, which fixed-size chunks could cut in half.
  * Retrieval is deterministic and auditable: the agent can cite "Manual, section 4.1".
  * No embedding model (Anthropic does not offer one) or vector database is needed.

The manual itself is in Portuguese, so section titles, keyword matching and the
stopword list below are in Portuguese on purpose.

Usage:
    uv run python -m emporio_agent.policies.manual       # prints the extracted sections
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from pypdf import PdfReader

from emporio_agent.config import get_settings
from emporio_agent.text import normalize_text

# Header/footer lines repeated on every page.
_PAGE_HEADER = re.compile(r"^\s*Empório da Música Manual de Políticas e Procedimentos\s*$")
_PAGE_FOOTER = re.compile(r"^\s*Página \d+\s*$")
# Closing footer of the document (last page).
_DOC_FOOTER = re.compile(r"^\s*(Empório da Música — Sua música começa aqui\.|Última atualização:)")
# Headings start at column 0; numbered lists in the body are indented and do not match.
_TOP_HEADING = re.compile(r"^(\d{1,2})\.\s+(\S.*)$")
_SUB_HEADING = re.compile(r"^(\d{1,2})\.(\d{1,2})\s+(\S.*)$")
_BULLET = re.compile(r"^\s*•\s+")
_NUMBERED = re.compile(r"^\s*(\d+)\.\s+")
_VERSION = re.compile(r"Versão\s+([\d.]+)\s+—\s+(.+?)\s*$")

# Topics exposed to the agent -> (keyword found in the top-level section title, description).
# Topics are matched by title (not by number) so they survive a renumbering of the manual.
TOPICS: dict[str, tuple[str, str]] = {
    "about_store": ("sobre", "About the store: address, phone, e-mail, and what the store does or does not sell"),
    "opening_hours": ("horario", "Opening hours of the store and of the WhatsApp service"),
    "payment": ("pagamento", "Payment methods, PIX discount and installment rules"),
    "returns_exchanges": ("trocas", "Right of withdrawal (regret), exchanges for defects or preference, ineligible items"),
    "shipping": ("frete", "Shipping, delivery times and tracking codes"),
    "promotions": ("promoc", "Promotional campaigns and promotion rules (they do not stack)"),
    "customer_service": ("atendimento", "Service guidelines, scope and special situations (complaints, etc.)"),
    "warranty": ("garantia", "Legal warranty, manufacturer warranty and what is not covered"),
    "privacy": ("privacidade", "Privacy and data protection (LGPD)"),
}

# Portuguese stopwords: the manual (and the search queries against it) are in Portuguese.

_STOPWORDS = set(
    "a o e de da do das dos em no na nos nas um uma para por com sem que se ao aos as os "
    "eu meu minha voces voce vcs vc qual quais como quando onde ser ter tem posso pode "
    "loja emporio musica e ou mais muito sobre isso esse essa este esta entre ate apos".split()
)


@dataclass
class PolicySection:
    section_id: str              # "4" or "4.1"
    title: str
    lines: list[str] = field(default_factory=list)
    parent_id: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip()

    def render(self, include_title: bool = True) -> str:
        body = self.text
        if not include_title:
            return body
        return f"## {self.section_id} {self.title}\n{body}".strip()


@dataclass
class PolicyManual:
    version: str
    updated_at: str
    sections: list[PolicySection]

    # ---------------------------------------------------------------- lookup
    def top_level(self) -> list[PolicySection]:
        return [s for s in self.sections if s.parent_id is None]

    def children(self, section_id: str) -> list[PolicySection]:
        return [s for s in self.sections if s.parent_id == section_id]

    def get_section(self, section_id: str) -> PolicySection | None:
        return next((s for s in self.sections if s.section_id == section_id), None)

    def render_section(self, section_id: str) -> str:
        """Full top-level section, including all of its subsections."""
        top = self.get_section(section_id)
        if top is None:
            raise KeyError(section_id)
        parts = [top.render()] + [child.render() for child in self.children(section_id)]
        return "\n\n".join(p for p in parts if p.strip())

    def section_for_topic(self, topic: str) -> PolicySection:
        if topic not in TOPICS:
            raise KeyError(f"Unknown topic: {topic}. Valid topics: {', '.join(TOPICS)}")
        keyword = TOPICS[topic][0]
        for section in self.top_level():
            if keyword in normalize_text(section.title):
                return section
        raise LookupError(f"No manual section matches topic '{topic}'")

    def get_topic(self, topic: str) -> str:
        return self.render_section(self.section_for_topic(topic).section_id)

    # ---------------------------------------------------------------- search
    def search(self, query: str, k: int = 3) -> list[tuple[PolicySection, float]]:
        """Keyword search (simple TF-IDF) over the manual's subsections.

        Used when a question does not clearly fit a topic. Returns the `k` most
        relevant sections with score > 0.
        """
        units = self._search_units()
        query_terms = _tokenize(query)
        if not query_terms:
            return []
        doc_terms = [Counter(_tokenize(f"{s.title} {s.title} {s.text}")) for s in units]  # title counts twice
        n_docs = len(units)
        df = Counter(term for terms in doc_terms for term in terms)
        scored = []
        for section, terms in zip(units, doc_terms):
            score = sum(
                (1 + math.log(terms[t])) * math.log(1 + n_docs / df[t])
                for t in query_terms if terms.get(t)
            )
            if score > 0:
                scored.append((section, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:k]

    def _search_units(self) -> list[PolicySection]:
        """Subsections + the introductory text of top-level sections (when present)."""
        return [s for s in self.sections if s.text]


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def _tokenize(text: str) -> list[str]:
    """Normalized tokens with a very simple (Portuguese) stemmer.

    Plurals are reduced ("trocas" -> "troca") and long words are truncated to 5
    letters, bringing inflections of the same root together: "arrependi" ~
    "arrependimento", "devolver" ~ "devolução", "parcelar" ~ "parcelamento".
    """
    tokens = re.findall(r"[a-z0-9]+", normalize_text(text))
    stemmed = []
    for tok in tokens:
        if tok in _STOPWORDS or len(tok) < 2:
            continue
        if tok.endswith("coes"):
            tok = tok[:-4] + "cao"
        elif tok.endswith("s") and len(tok) > 3:
            tok = tok[:-1]
        stemmed.append(tok[:5])
    return stemmed


def _clean_line(raw: str) -> str | None:
    """Normalizes a layout line: bullets become '- ', table columns become ' | '."""
    if _BULLET.match(raw):
        return "- " + _BULLET.sub("", raw).strip()
    numbered = _NUMBERED.match(raw)
    if numbered and raw.startswith(" "):
        return f"{numbered.group(1)}. " + raw[numbered.end():].strip()
    stripped = raw.strip()
    if not stripped:
        return None
    return re.sub(r"\s{3,}", " | ", stripped)


def _is_continuation(raw: str) -> bool:
    """Wrapped line of a bullet/cell: deeply indented and without inner columns."""
    leading = len(raw) - len(raw.lstrip(" "))
    return leading >= 5 and not re.search(r"\S\s{3,}\S", raw) and not _BULLET.match(raw)


def parse_manual(pdf_path: Path) -> PolicyManual:
    reader = PdfReader(str(pdf_path))
    pages = [page.extract_text(extraction_mode="layout") for page in reader.pages]

    cover = pages[0] if pages else ""
    version_match = next((m for m in map(_VERSION.search, cover.splitlines()) if m), None)
    version = version_match.group(1) if version_match else "unknown"
    updated_at = version_match.group(2) if version_match else "unknown"

    sections: list[PolicySection] = []
    current: PolicySection | None = None

    for page in pages[1:]:  # the cover page has no sections
        for raw in page.splitlines():
            if _PAGE_HEADER.match(raw) or _PAGE_FOOTER.match(raw) or _DOC_FOOTER.match(raw):
                continue
            if sub := _SUB_HEADING.match(raw):
                parent_id = sub.group(1)
                current = PolicySection(f"{parent_id}.{sub.group(2)}", sub.group(3).strip(), parent_id=parent_id)
                sections.append(current)
                continue
            if top := _TOP_HEADING.match(raw):
                current = PolicySection(top.group(1), top.group(2).strip())
                sections.append(current)
                continue
            if current is None:
                continue
            if _is_continuation(raw) and current.lines:
                current.lines[-1] = f"{current.lines[-1]} {raw.strip()}"
                continue
            line = _clean_line(raw)
            if line is not None:
                current.lines.append(line)

    if not sections:
        raise ValueError(f"No sections found in the manual: {pdf_path}")
    return PolicyManual(version=version, updated_at=updated_at, sections=sections)


@lru_cache(maxsize=4)
def _load_cached(pdf_path: Path, mtime: float) -> PolicyManual:
    return parse_manual(pdf_path)


def load_manual(pdf_path: Path | None = None) -> PolicyManual:
    """Parsed manual (cached; re-parsed when the PDF changes)."""
    pdf_path = pdf_path or get_settings().policies_pdf
    return _load_cached(pdf_path, pdf_path.stat().st_mtime)


def main() -> None:
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    manual = load_manual()
    print(f"Manual version {manual.version} ({manual.updated_at}) — {len(manual.sections)} sections\n")
    for topic in TOPICS:
        section = manual.section_for_topic(topic)
        print(f"[{topic}] -> section {section.section_id} {section.title}")
    print()
    for section in manual.top_level():
        print("=" * 80)
        print(manual.render_section(section.section_id))
        print()


if __name__ == "__main__":
    main()
