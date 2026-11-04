"""Testes da sessão com histórico em memória e do loop do CLI (cliente falso, sem rede)."""

from __future__ import annotations

from emporio_agent.agent import Agent, ChatSession
from emporio_agent.agent.session import trim_history
from emporio_agent.interface.cli import run
from tests.fakes import FakeClient, response, text, tool_use


def make_session(executor, settings, responses) -> tuple[ChatSession, FakeClient]:
    client = FakeClient(responses)
    return ChatSession(Agent(executor, client=client, settings=settings)), client


# --------------------------------------------------------------------------- #
# Sessão / histórico
# --------------------------------------------------------------------------- #
def test_follow_up_question_sees_previous_tool_result(executor, settings) -> None:
    """'E quanto fica à vista no PIX?' precisa do produto e dos dados do turno anterior."""
    session, client = make_session(executor, settings, [
        response(tool_use("t1", "detalhes_produto", {"produto_id": 81}), stop="tool_use"),
        response(text("O Yamaha C40 sai por R$ 599,90.")),
        response(text("No PIX fica R$ 569,90.")),
    ])
    session.send("Quanto custa o violão Yamaha C40?")
    reply = session.send("E quanto fica à vista no PIX?")

    assert reply.text == "No PIX fica R$ 569,90."
    sent = client.requests[-1]["messages"]
    assert sent[0]["content"] == "Quanto custa o violão Yamaha C40?"
    assert sent[-1] == {"role": "user", "content": "E quanto fica à vista no PIX?"}
    tool_result = sent[2]["content"][0]
    assert tool_result["type"] == "tool_result" and "R$ 569,90" in tool_result["content"]
    assert session.turns == 2


def test_reset_clears_history(executor, settings) -> None:
    session, client = make_session(executor, settings, [response(text("Oi!")), response(text("Olá de novo!"))])
    session.send("oi")
    session.reset()
    session.send("oi de novo")

    assert client.requests[-1]["messages"] == [{"role": "user", "content": "oi de novo"}]
    assert session.turns == 1


def test_failed_turn_is_not_kept(executor, settings) -> None:
    session, _ = make_session(executor, settings, [response(text("Oi!")), response(stop="end_turn")])
    session.send("oi")
    reply = session.send("???")  # resposta vazia -> fallback

    assert reply.error
    assert session.turns == 1


def test_trim_history_cuts_at_user_text_turn() -> None:
    turn_with_tool = [
        {"role": "user", "content": "pergunta"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "x", "name": "n", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "{}"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "resposta"}]},
    ]
    history = turn_with_tool * 5  # 20 mensagens
    trimmed = trim_history(history, limit=7)

    assert len(trimmed) == 4
    assert trimmed[0] == {"role": "user", "content": "pergunta"}
    assert trim_history(history, limit=50) is history


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def scripted(lines: list[str]):
    it = iter(lines)

    def read(_prompt: str) -> str:
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    return read


def test_cli_multi_turn_conversation(executor, settings) -> None:
    session, client = make_session(executor, settings, [
        response(tool_use("t1", "detalhes_produto", {"produto_id": 81}), stop="tool_use"),
        response(text("Temos sim! R$ 599,90.")),
        response(text("No PIX, R$ 569,90.")),
    ])
    out: list[str] = []
    run(session, read=scripted(["Tem Yamaha C40?", "", "E no PIX?", "/sair"]), write=out.append, debug=True)

    joined = "\n".join(out)
    assert "Empório: Temos sim! R$ 599,90." in joined
    assert "Empório: No PIX, R$ 569,90." in joined
    assert "[tool] detalhes_produto" in joined
    assert len(client.requests) == 3  # linha vazia e /sair não chamam o modelo
    assert out[-1].startswith("Até mais")


def test_cli_commands(executor, settings) -> None:
    session, client = make_session(executor, settings, [response(text("Oi!")), response(text("Oi de novo!"))])
    out: list[str] = []
    run(session, read=scripted(["oi", "/ajuda", "/novo", "oi"]), write=out.append)

    joined = "\n".join(out)
    assert "/novo" in joined and "(nova conversa iniciada)" in joined
    assert client.requests[-1]["messages"] == [{"role": "user", "content": "oi"}]
    assert "[tool]" not in joined
    assert "Até mais" in out[-1]  # EOF encerra sem erro
