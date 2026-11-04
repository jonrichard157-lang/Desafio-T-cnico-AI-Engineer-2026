"""Loop de tool calling do agente, usando a Messages API da Anthropic diretamente.

Fluxo de uma mensagem do cliente:
  1. Envia histórico + mensagem nova + tools ao modelo.
  2. Se o modelo pedir ferramentas (`stop_reason == "tool_use"`), executa todas,
     devolve os `tool_result` e chama o modelo de novo.
  3. Repete até o modelo responder em texto, ou até o limite de rodadas.

O histórico é mantido como lista de dicts JSON-serializáveis (formato da API),
para poder ser persistido por sessão nas interfaces (CLI/API).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import anthropic

from emporio_agent.agent.prompt import build_system
from emporio_agent.config import Settings, get_settings
from emporio_agent.tools.base import ToolExecutor

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6

FALLBACK_ERROR = (
    "Poxa, tive um probleminha técnico aqui e não consegui consultar o sistema agora. 😕 "
    "Pode tentar de novo em instantes? Se preferir, fale com a equipe pelo WhatsApp (67) 3341-4444."
)
FALLBACK_TOO_MANY_STEPS = (
    "Desculpa, não consegui concluir essa consulta. Pode me contar de novo, de um jeito mais direto, "
    "o que você está procurando?"
)


@dataclass
class ToolCall:
    """Registro de uma chamada de ferramenta (para logs, testes e depuração)."""

    name: str
    input: dict
    output: str
    is_error: bool


@dataclass
class AgentReply:
    text: str
    messages: list[dict]                       # histórico completo, já com esta troca
    tool_calls: list[ToolCall] = field(default_factory=list)
    error: str | None = None                   # preenchido quando houve fallback


def _block_to_dict(block: Any) -> dict | None:
    """Converte um bloco de conteúdo da resposta em dict aceito de volta pela API."""
    if block.type == "text":
        return {"type": "text", "text": block.text}
    if block.type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    return None  # outros tipos (ex.: thinking) não são usados neste agente


def _text_of(content: list[dict]) -> str:
    return "\n".join(b["text"] for b in content if b["type"] == "text").strip()


class Agent:
    def __init__(
        self,
        executor: ToolExecutor,
        client: Any | None = None,
        settings: Settings | None = None,
        max_tool_rounds: int = MAX_TOOL_ROUNDS,
    ) -> None:
        self.settings = settings or get_settings()
        self.executor = executor
        self.client = client or anthropic.Anthropic(api_key=self.settings.anthropic_api_key or None)
        self.max_tool_rounds = max_tool_rounds
        self._tools = executor.anthropic_schemas()

    def _create(self, messages: list[dict]) -> Any:
        return self.client.messages.create(
            model=self.settings.anthropic_model,
            max_tokens=self.settings.anthropic_max_tokens,
            system=build_system(self.settings.now(), self.settings.partner_stores),
            tools=self._tools,
            messages=messages,
        )

    def reply(self, user_message: str, history: list[dict] | None = None) -> AgentReply:
        """Processa uma mensagem do cliente e devolve a resposta e o histórico atualizado.

        `history` não é modificado; em caso de erro, o histórico devolvido não inclui
        a troca que falhou (para não deixar um `tool_use` sem `tool_result`).
        """
        base = list(history or [])
        messages = base + [{"role": "user", "content": user_message}]
        calls: list[ToolCall] = []

        for _ in range(self.max_tool_rounds + 1):
            try:
                response = self._create(messages)
            except anthropic.APIError as exc:
                logger.exception("Falha na chamada ao modelo")
                return AgentReply(FALLBACK_ERROR, base, calls, error=f"{type(exc).__name__}: {exc}")

            content = [d for d in map(_block_to_dict, response.content) if d]
            messages.append({"role": "assistant", "content": content})

            if response.stop_reason != "tool_use":
                if response.stop_reason == "max_tokens":
                    logger.warning("Resposta truncada por max_tokens")
                text = _text_of(content)
                if not text:
                    return AgentReply(FALLBACK_ERROR, base, calls, error="resposta vazia do modelo")
                return AgentReply(text, messages, calls)

            results = []
            for block in content:
                if block["type"] != "tool_use":
                    continue
                result = self.executor.execute(block["name"], block["input"])
                logger.info("tool %s(%s) -> erro=%s", block["name"], json.dumps(block["input"], ensure_ascii=False),
                            result.is_error)
                calls.append(ToolCall(block["name"], block["input"], result.content, result.is_error))
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": result.content,
                    "is_error": result.is_error,
                })
            messages.append({"role": "user", "content": results})

        logger.warning("Limite de %d rodadas de ferramentas atingido", self.max_tool_rounds)
        return AgentReply(FALLBACK_TOO_MANY_STEPS, base, calls, error="limite de rodadas de ferramentas")


def build_agent(client: Any | None = None) -> Agent:
    """Agente com as dependências reais (banco, manual e cliente da Anthropic)."""
    from emporio_agent.tools import build_executor

    return Agent(build_executor(), client=client)
