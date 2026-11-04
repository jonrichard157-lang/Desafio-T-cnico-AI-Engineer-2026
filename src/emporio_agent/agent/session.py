"""Sessão de conversa com histórico em memória.

Guarda o histórico no formato da Messages API (incluindo tool_use/tool_result),
para que perguntas de acompanhamento ("E no PIX, quanto fica?") tenham o
contexto do turno anterior: o modelo vê qual produto foi consultado e os dados
que a ferramenta devolveu.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from emporio_agent.agent.core import Agent, AgentReply

# Limite de mensagens mantidas no histórico (cada turno com ferramenta usa ~4).
MAX_HISTORY_MESSAGES = 60


def trim_history(messages: list[dict], limit: int = MAX_HISTORY_MESSAGES) -> list[dict]:
    """Descarta os turnos mais antigos, cortando sempre no início de um turno do cliente.

    O corte nunca separa um `tool_use` do seu `tool_result`: o histórico recomeça
    numa mensagem de usuário com texto (não numa lista de tool_result).
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
        """Quantidade de mensagens do cliente no histórico."""
        return sum(1 for m in self.history if m["role"] == "user" and isinstance(m["content"], str))
