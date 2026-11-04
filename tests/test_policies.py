"""Tests of the policy manual parsing (PDF -> sections)."""

from __future__ import annotations

import pytest

from emporio_agent.config import PROJECT_ROOT
from emporio_agent.policies.manual import TOPICS, PolicyManual, load_manual


@pytest.fixture(scope="module")
def manual() -> PolicyManual:
    return load_manual(PROJECT_ROOT / "data" / "políticas_da_loja.pdf")


def test_metadata(manual: PolicyManual) -> None:
    assert manual.version == "2.1"
    assert manual.updated_at == "Junho de 2025"


def test_all_top_level_sections_found(manual: PolicyManual) -> None:
    assert [s.section_id for s in manual.top_level()] == [str(i) for i in range(1, 11)]


def test_subsections_found(manual: PolicyManual) -> None:
    ids = {s.section_id for s in manual.sections}
    assert {"1.2", "3.1", "4.1", "4.2", "4.3", "4.4", "5.1", "5.2", "5.3", "6.2", "7.1", "7.2", "7.3", "8.3"} <= ids


@pytest.mark.parametrize("topic", list(TOPICS))
def test_every_topic_resolves(manual: PolicyManual, topic: str) -> None:
    assert manual.get_topic(topic).startswith("## ")


def test_headers_and_footers_removed(manual: PolicyManual) -> None:
    full = "\n".join(manual.render_section(s.section_id) for s in manual.top_level())
    assert "Página " not in full
    assert "Manual de Políticas e Procedimentos" not in full
    assert "Última atualização" not in full


def test_tables_are_preserved(manual: PolicyManual) -> None:
    assert "Sábado | 09:00 às 13:00" in manual.get_topic("opening_hours")
    assert "Endereço | Rua 14 de Maio, 3200" in manual.get_topic("about_store")


def test_wrapped_bullets_are_joined(manual: PolicyManual) -> None:
    text = manual.get_section("4.1").text
    assert "- O cliente pode solicitar a devolução em até 7 (sete) dias corridos após o recebimento do produto" in text


def test_numbered_list_spanning_pages(manual: PolicyManual) -> None:
    text = manual.get_section("7.2").text
    for step in ("1. Saudação", "3. Consulta", "5. Fechamento"):
        assert step in text


def test_topic_includes_subsections(manual: PolicyManual) -> None:
    text = manual.get_topic("returns_exchanges")
    assert "## 4.1 Direito de Arrependimento" in text
    assert "## 4.4 Itens Não Elegíveis para Troca" in text


@pytest.mark.parametrize(
    ("question", "expected_section"),
    [
        ("Me arrependi da compra, posso devolver?", "4.1"),
        ("Meu violão veio com defeito, posso trocar?", "4.2"),
        ("Em quantas vezes posso parcelar no cartão?", "3.1"),
        ("Quanto tempo demora a entrega pelo SEDEX?", "5.2"),
        ("Qual o endereço da loja?", "1.2"),
        ("O desconto do pix vale junto com a promoção?", "6.2"),
        ("Qual o prazo da garantia legal?", "8.1"),
    ],
)
def test_keyword_search(manual: PolicyManual, question: str, expected_section: str) -> None:
    results = manual.search(question, k=3)
    assert expected_section in [section.section_id for section, _ in results]


def test_search_without_meaningful_terms(manual: PolicyManual) -> None:
    assert manual.search("o a de") == []

