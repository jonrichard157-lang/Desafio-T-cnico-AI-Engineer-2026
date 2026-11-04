"""Infraestrutura comum das ferramentas (tools) expostas ao modelo.

Cada ferramenta é composta por:
  * um nome e uma descrição -> é o que o modelo lê para DECIDIR quando usá-la;
  * um modelo Pydantic de entrada -> gera o JSON Schema enviado à API da Anthropic
    e VALIDA os argumentos que o modelo devolve antes de executarmos qualquer coisa;
  * uma função Python (handler) -> executa a consulta de forma determinística.

O resultado é sempre um JSON (texto), que volta ao modelo como `tool_result`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from emporio_agent.policies.manual import PolicyManual

logger = logging.getLogger(__name__)


@dataclass
class ToolContext:
    """Dependências compartilhadas pelas ferramentas (injetadas, facilitando testes)."""

    conn: sqlite3.Connection
    manual: PolicyManual
    today: Callable[[], date]


@dataclass
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    handler: Callable[[ToolContext, Any], dict]

    def to_anthropic(self) -> dict:
        """Formato esperado pelo parâmetro `tools` da Messages API."""
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return {"name": self.name, "description": self.description, "input_schema": schema}


@dataclass
class ToolResult:
    content: str          # JSON enviado de volta ao modelo
    is_error: bool = False
    data: dict = field(default_factory=dict)


def to_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=None, default=str)


class ToolExecutor:
    """Executa chamadas de ferramenta vindas do modelo, com validação e tratamento de erro.

    Erros nunca derrubam a conversa: viram um `tool_result` com `is_error=True`
    e uma mensagem clara, para que o modelo possa corrigir os argumentos ou
    explicar a situação ao cliente.
    """

    def __init__(self, tools: list[Tool], context: ToolContext) -> None:
        self._tools = {tool.name: tool for tool in tools}
        self._context = context

    @property
    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    def anthropic_schemas(self) -> list[dict]:
        return [tool.to_anthropic() for tool in self._tools.values()]

    def execute(self, name: str, arguments: dict | None) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(to_json({"erro": f"Ferramenta desconhecida: {name}"}), is_error=True)
        try:
            params = tool.input_model.model_validate(arguments or {})
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'entrada'}: {e['msg']}" for e in exc.errors())
            return ToolResult(to_json({"erro": f"Argumentos inválidos: {problems}"}), is_error=True)
        try:
            data = tool.handler(self._context, params)
        except Exception as exc:  # noqa: BLE001 — qualquer falha vira resposta controlada
            logger.exception("Falha ao executar a ferramenta %s", name)
            return ToolResult(to_json({"erro": f"Falha interna ao consultar o sistema: {exc}"}), is_error=True)
        return ToolResult(to_json(data), data=data)


# --------------------------------------------------------------------------- #
# Formatação (feita no código para o modelo não errar contas nem formatos)
# --------------------------------------------------------------------------- #
def brl(value: float | None) -> str | None:
    """2199.0 -> 'R$ 2.199,00'"""
    if value is None:
        return None
    formatted = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}"


def br_date(iso: str | date | None) -> str | None:
    """'2026-02-17' -> '17/02/2026'"""
    if not iso:
        return None
    value = iso if isinstance(iso, date) else date.fromisoformat(iso)
    return value.strftime("%d/%m/%Y")

