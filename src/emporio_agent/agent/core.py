"""The agent's tool-calling loop, using Anthropic's Messages API directly.

Flow for one customer message:
  1. Send history + new message + tools to the model.
  2. If the model asks for tools (`stop_reason == "tool_use"`), run all of them,
     send back the `tool_result` blocks and call the model again.
  3. Repeat until the model answers with text, or the round limit is reached.

The history is kept as a list of JSON-serializable dicts (the API format), so an
interface can store it per session.

Fallback messages are shown to customers, so they are in Brazilian Portuguese.
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
    "Pode tentar de novo em instantes? Se preferir, fale com a equipe pelo WhatsApp (67) 3321-4500."
)
FALLBACK_TOO_MANY_STEPS = (
    "Desculpa, não consegui concluir essa consulta. Pode me contar de novo, de um jeito mais direto, "
    "o que você está procurando?"
)


@dataclass
class ToolCall:
    """Record of one tool call (for logs, tests and debugging)."""

    name: str
    input: dict
    output: str
    is_error: bool


@dataclass
class AgentReply:
    text: str
    messages: list[dict]                       # full history, including this exchange
    tool_calls: list[ToolCall] = field(default_factory=list)
    error: str | None = None                   # set when a fallback reply was used


def _block_to_dict(block: Any) -> dict | None:
    """Converts a response content block into a dict the API accepts back."""
    if block.type == "text":
        return {"type": "text", "text": block.text}
    if block.type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    return None  # other types (e.g. thinking) are not used by this agent


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
        """Processes one customer message and returns the reply and the updated history.

        `history` is not modified; on error, the returned history leaves out the failed
        exchange (so no `tool_use` is ever left without its `tool_result`).
        """
        base = list(history or [])
        messages = base + [{"role": "user", "content": user_message}]
        calls: list[ToolCall] = []

        for _ in range(self.max_tool_rounds + 1):
            try:
                response = self._create(messages)
            except anthropic.APIError as exc:
                logger.exception("Model call failed")
                return AgentReply(FALLBACK_ERROR, base, calls, error=f"{type(exc).__name__}: {exc}")

            content = [d for d in map(_block_to_dict, response.content) if d]
            messages.append({"role": "assistant", "content": content})

            if response.stop_reason != "tool_use":
                if response.stop_reason == "max_tokens":
                    logger.warning("Reply truncated by max_tokens")
                text = _text_of(content)
                if not text:
                    return AgentReply(FALLBACK_ERROR, base, calls, error="empty reply from the model")
                return AgentReply(text, messages, calls)

            results = []
            for block in content:
                if block["type"] != "tool_use":
                    continue
                result = self.executor.execute(block["name"], block["input"])
                logger.info("tool %s(%s) -> error=%s", block["name"], json.dumps(block["input"], ensure_ascii=False),
                            result.is_error)
                calls.append(ToolCall(block["name"], block["input"], result.content, result.is_error))
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": result.content,
                    "is_error": result.is_error,
                })
            messages.append({"role": "user", "content": results})

        logger.warning("Tool round limit (%d) reached", self.max_tool_rounds)
        return AgentReply(FALLBACK_TOO_MANY_STEPS, base, calls, error="tool round limit reached")


def build_agent(client: Any | None = None) -> Agent:
    """Agent wired with its real dependencies (database, manual and Anthropic client)."""
    from emporio_agent.tools import build_executor

    return Agent(build_executor(), client=client)
