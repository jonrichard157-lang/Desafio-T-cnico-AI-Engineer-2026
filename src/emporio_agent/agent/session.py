"""Conversation session with in-memory history.

Keeps the history in the Messages API format (including tool_use/tool_result), so
follow-up questions ("E no PIX, quanto fica?" / "And with PIX, how much?") have
the previous turn's context: the model sees which product was looked up and the
data the tool returned.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from emporio_agent.agent.core import Agent, AgentReply

# Maximum number of messages kept in the history (a turn with tools uses ~4).
MAX_HISTORY_MESSAGES = 60


def trim_history(messages: list[dict], limit: int = MAX_HISTORY_MESSAGES) -> list[dict]:
    """Drops the oldest turns, always cutting at the start of a customer turn.

    The cut never separates a `tool_use` from its `tool_result`: the history restarts
    at a user message with text (not at a list of tool_result blocks).
    """
    if len(messages) <= limit:
        return messages
    for start in range(len(messages) - limit, len(messages)):
        msg = messages[start]
        if msg["role"] == "user" and isinstance(msg["content"], str):
            return messages[start:]
    return []


@dataclass
class ChatSession:
    agent: Agent
    history: list[dict] = field(default_factory=list)
    max_messages: int = MAX_HISTORY_MESSAGES

    def send(self, user_message: str) -> AgentReply:
        reply = self.agent.reply(user_message, self.history)
        self.history = trim_history(reply.messages, self.max_messages)
        return reply

    def reset(self) -> None:
        self.history = []

    @property
    def turns(self) -> int:
        """Number of customer messages in the history."""
        return sum(1 for m in self.history if m["role"] == "user" and isinstance(m["content"], str))
