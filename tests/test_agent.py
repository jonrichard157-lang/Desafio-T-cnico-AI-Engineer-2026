"""Testes do prompt e do loop de tool calling, com um cliente falso (sem chamar a API)."""

from __future__ import annotations

import json
from datetime import datetime

import anthropic
import httpx
import pytest

from emporio_agent.agent import Agent, build_system
from emporio_agent.agent.core import FALLBACK_ERROR, FALLBACK_TOO_MANY_STEPS
from emporio_agent.agent.prompt import STATIC_PROMPT, build_context_block
from emporio_agent.tools import ALL_TOOLS, ToolExecutor
from tests.fakes import FakeClient, response, text, tool_use


def make_agent(executor, settings, responses, **kwargs) -> tuple[Agent, FakeClient]:
    client = FakeClient(responses)
    return Agent(executor, client=client, settings=settings, **kwargs), client


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #
def test_prompt_has_core_rules() -> None:
    lowered = STATIC_PROMPT.lower()
    assert "amigo" in lowered
    assert "nunca diga que um produto está disponível" in lowered
    for accessory in ("cordas", "cabos", "pedais"):
        assert accessory in lowered
    assert "5%" in STATIC_PROMPT and "não acumula com promoção" in lowered
    # divergências do manual resolvidas no prompt (ver README)
    assert "(67) 3321-4500" in STATIC_PROMPT and "contato@emporiodamusica.com.br" in STATIC_PROMPT


def test_system_caches_static_part_and_adds_context() -> None:
    system = build_system(datetime(2026, 3, 14, 15, 30), ("Loja A - (67) 1111-1111",))
    assert system[0]["text"] == STATIC_PROMPT
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in system[1]
    assert "sábado, 14/03/2026, 15:30" in system[1]["text"]
    assert "Loja A - (67) 1111-1111" in system[1]["text"]


def test_context_without_partners_forbids_inventing_stores() -> None:
    block = build_context_block(datetime(2026, 3, 10, 10, 0))
    assert "Não há lojas parceiras" in block
    assert "terça-feira" in block


# --------------------------------------------------------------------------- #
# Loop de tool calling
# --------------------------------------------------------------------------- #
def test_plain_answer_without_tools(executor, settings) -> None:
    agent, client = make_agent(executor, settings, [response(text("Oi! Como posso ajudar?"))])
    reply = agent.reply("oi")

    assert reply.text == "Oi! Como posso ajudar?"
    assert reply.tool_calls == []
    assert [m["role"] for m in reply.messages] == ["user", "assistant"]
    request = client.requests[0]
    assert request["model"] == "modelo-teste"
    assert {t["name"] for t in request["tools"]} == {t.name for t in ALL_TOOLS}
    assert "Loja Parceira X" in request["system"][1]["text"]


def test_tool_round_trip(executor, settings) -> None:
    agent, client = make_agent(executor, settings, [
        response(text("Vou ver aqui!"), tool_use("t1", "buscar_produtos", {"categoria": "Violões"}), stop="tool_use"),
        response(text("Achei estas opções...")),
    ])
    reply = agent.reply("tem violão?")

    assert reply.text == "Achei estas opções..."
    assert [c.name for c in reply.tool_calls] == ["buscar_produtos"]
    assert not reply.tool_calls[0].is_error

    # 2ª chamada recebe o tool_use e o tool_result com o JSON real da ferramenta
    second = client.requests[1]["messages"]
    assert second[1]["content"][1] == {"type": "tool_use", "id": "t1", "name": "buscar_produtos",
                                       "input": {"categoria": "Violões"}}
    result = second[2]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "t1" and result["is_error"] is False
    assert "Violões" in result["content"]
    assert [m["role"] for m in reply.messages] == ["user", "assistant", "user", "assistant"]


def test_parallel_tool_calls_answered_together(executor, settings) -> None:
    agent, client = make_agent(executor, settings, [
        response(
            tool_use("a", "consultar_politicas", {"topico": "horario"}),
            tool_use("b", "consultar_politicas", {"topico": "pagamento"}),
            stop="tool_use",
        ),
        response(text("Funcionamos ... e aceitamos PIX ...")),
    ])
    agent.reply("que horas abre e aceita pix?")

    results = client.requests[1]["messages"][-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["a", "b"]


def test_invalid_tool_arguments_return_error_to_model(executor, settings) -> None:
    agent, client = make_agent(executor, settings, [
        response(tool_use("t1", "buscar_produtos", {}), stop="tool_use"),
        response(text("Me conta o que você procura?")),
    ])
    reply = agent.reply("quero algo")

    assert reply.tool_calls[0].is_error
    assert client.requests[1]["messages"][-1]["content"][0]["is_error"] is True
    assert reply.error is None


def test_history_is_reused_and_not_mutated(executor, settings) -> None:
    history = [{"role": "user", "content": "oi"}, {"role": "assistant", "content": [{"type": "text", "text": "Olá!"}]}]
    agent, client = make_agent(executor, settings, [response(text("Claro!"))])
    reply = agent.reply("e aí?", history)

    assert len(history) == 2
    assert client.requests[0]["messages"][:2] == history
    assert len(reply.messages) == 4


def test_api_error_returns_fallback_and_keeps_history(executor, settings) -> None:
    error = anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))
    history = [{"role": "user", "content": "oi"}, {"role": "assistant", "content": [{"type": "text", "text": "Olá!"}]}]
    agent, _ = make_agent(executor, settings, [error])
    reply = agent.reply("tem guitarra?", history)

    assert reply.text == FALLBACK_ERROR
    assert reply.error
    assert reply.messages == history


def test_tool_round_limit(executor, settings) -> None:
    loop = response(tool_use("t", "consultar_politicas", {"topico": "horario"}), stop="tool_use")
    agent, client = make_agent(executor, settings, [loop, loop, loop], max_tool_rounds=2)
    reply = agent.reply("?")

    assert reply.text == FALLBACK_TOO_MANY_STEPS
    assert len(client.requests) == 3
    assert reply.messages == []
