"""Processamento do Manual de Políticas e Procedimentos (PDF) em seções consultáveis.

Por que seções e não RAG vetorial?
  * O manual é pequeno (~15 mil caracteres, 10 seções numeradas) e já vem organizado
    por assunto. Dividir pela própria numeração (1., 4.1, ...) preserva o contexto
    completo de cada regra, coisa que chunks de tamanho fixo podem quebrar.
  * A busca é determinística e auditável: dá para citar "Manual, seção 4.1".
  * Dispensa um modelo de embeddings (a Anthropic não oferece um) e um banco vetorial.

Uso:
    uv run python -m emporio_agent.policies.manual       # imprime as seções extraídas
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

# Linhas de cabeçalho/rodapé repetidas em todas as páginas.
_PAGE_HEADER = re.compile(r"^\s*Empório da Música Manual de Políticas e Procedimentos\s*$")
_PAGE_FOOTER = re.compile(r"^\s*Página \d+\s*$")
# Rodapé de encerramento do documento (última página).
_DOC_FOOTER = re.compile(r"^\s*(Empório da Música — Sua música começa aqui\.|Última atualização:)")
# Títulos começam na coluna 0; listas numeradas do texto são indentadas e não casam.
_TOP_HEADING = re.compile(r"^(\d{1,2})\.\s+(\S.*)$")
_SUB_HEADING = re.compile(r"^(\d{1,2})\.(\d{1,2})\s+(\S.*)$")
_BULLET = re.compile(r"^\s*•\s+")
_NUMBERED = re.compile(r"^\s*(\d+)\.\s+")
_VERSION = re.compile(r"Versão\s+([\d.]+)\s+—\s+(.+?)\s*$")

# Tópicos expostos ao agente -> palavras que identificam o título da seção de nível 1.
# Mapeamos pelo título (e não pelo número) para resistir a renumerações do manual.
TOPICS: dict[str, tuple[str, str]] = {
    "sobre_a_loja": ("sobre", "Sobre a loja, endereço, telefone, e-mail e o que a loja vende ou não vende"),
    "horario": ("horario", "Horário de funcionamento da loja e do WhatsApp"),
    "pagamento": ("pagamento", "Formas de pagamento, desconto no PIX e regras de parcelamento"),
    "trocas_devolucoes": ("trocas", "Arrependimento, trocas por defeito ou preferência e itens não elegíveis"),
    "frete_entrega": ("frete", "Frete, prazos de entrega e código de rastreamento"),
    "promocoes": ("promoc", "Campanhas promocionais e regras de promoções (não cumulativas)"),
    "atendimento": ("atendimento", "Diretrizes de atendimento, escopo e situações especiais (reclamações etc.)"),
    "garantia": ("garantia", "Garantia legal, garantia do fabricante e o que não é coberto"),
    "privacidade": ("privacidade", "Privacidade e proteção de dados (LGPD)"),
}

_STOPWORDS = set(
    "a o e de da do das dos em no na nos nas um uma para por com sem que se ao aos as os "
    "eu meu minha voces voce vcs vc qual quais como quando onde ser ter tem posso pode "
    "loja emporio musica e ou mais muito sobre isso esse essa este esta".split()
)


@dataclass
class PolicySection:
    section_id: str              # "4" ou "4.1"
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
        """Seção de nível 1 completa, incluindo todas as subseções."""
        top = self.get_section(section_id)
        if top is None:
            raise KeyError(section_id)
        parts = [top.render()] + [child.render() for child in self.children(section_id)]
        return "\n\n".join(p for p in parts if p.strip())

    def section_for_topic(self, topic: str) -> PolicySection:
        if topic not in TOPICS:
            raise KeyError(f"Tópico desconhecido: {topic}. Válidos: {', '.join(TOPICS)}")
        keyword = TOPICS[topic][0]
        for section in self.top_level():
            if keyword in normalize_text(section.title):
                return section
        raise LookupError(f"Nenhuma seção do manual corresponde ao tópico '{topic}'")

    def get_topic(self, topic: str) -> str:
        return self.render_section(self.section_for_topic(topic).section_id)

    # ---------------------------------------------------------------- search
    def search(self, query: str, k: int = 3) -> list[tuple[PolicySection, float]]:
        """Busca por palavras-chave (TF-IDF simples) nas subseções do manual.

        Usada quando a pergunta não se encaixa claramente em um tópico. Retorna as
        `k` seções mais relevantes com score > 0.
        """
        units = self._search_units()
        query_terms = _tokenize(query)
        if not query_terms:
            return []
        doc_terms = [Counter(_tokenize(f"{s.title} {s.title} {s.text}")) for s in units]  # título com peso 2
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
        """Subseções + o texto introdutório das seções de nível 1 (quando houver)."""
        return [s for s in self.sections if s.text]


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", normalize_text(text))
    # Radical simples (plural): "trocas" ~ "troca", "devolucoes" ~ "devolucao".
    stemmed = []
    for tok in tokens:
        if tok in _STOPWORDS or len(tok) < 2:
            continue
        if tok.endswith("coes"):
            tok = tok[:-4] + "cao"
        elif tok.endswith("s") and len(tok) > 3:
            tok = tok[:-1]
        stemmed.append(tok)
    return stemmed


def _clean_line(raw: str) -> str | None:
    """Normaliza uma linha do layout: bullets viram '- ', colunas de tabela viram ' | '."""
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
    """Linha quebrada de um bullet/célula: bem indentada e sem colunas internas."""
    leading = len(raw) - len(raw.lstrip(" "))
    return leading >= 5 and not re.search(r"\S\s{3,}\S", raw) and not _BULLET.match(raw)


def parse_manual(pdf_path: Path) -> PolicyManual:
    reader = PdfReader(str(pdf_path))
    pages = [page.extract_text(extraction_mode="layout") for page in reader.pages]

    cover = pages[0] if pages else ""
    version_match = next((m for m in map(_VERSION.search, cover.splitlines()) if m), None)
    version = version_match.group(1) if version_match else "desconhecida"
    updated_at = version_match.group(2) if version_match else "desconhecida"

    sections: list[PolicySection] = []
    current: PolicySection | None = None

    for page in pages[1:]:  # a capa não tem seções
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
        raise ValueError(f"Nenhuma seção encontrada no manual: {pdf_path}")
    return PolicyManual(version=version, updated_at=updated_at, sections=sections)


@lru_cache(maxsize=4)
def _load_cached(pdf_path: Path, mtime: float) -> PolicyManual:
    return parse_manual(pdf_path)


def load_manual(pdf_path: Path | None = None) -> PolicyManual:
    """Manual processado (em cache; reprocessa se o PDF for alterado)."""
    pdf_path = pdf_path or get_settings().policies_pdf
    return _load_cached(pdf_path, pdf_path.stat().st_mtime)


def main() -> None:
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    manual = load_manual()
    print(f"Manual versão {manual.version} ({manual.updated_at}) — {len(manual.sections)} seções\n")
    for topic in TOPICS:
        section = manual.section_for_topic(topic)
        print(f"[{topic}] -> seção {section.section_id} {section.title}")
    print()
    for section in manual.top_level():
        print("=" * 80)
        print(manual.render_section(section.section_id))
        print()


if __name__ == "__main__":
    main()
