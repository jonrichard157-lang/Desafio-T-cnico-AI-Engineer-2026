"""Shared infrastructure for the tools exposed to the model.

Each tool is made of:
  * a name and a description -> what the model reads to DECIDE when to use it;
  * a Pydantic input model -> generates the JSON Schema sent to the Anthropic API
    and VALIDATES the arguments the model returns before anything runs;
  * a Python function (handler) -> runs the lookup deterministically.

The result is always JSON (text), sent back to the model as a `tool_result`.
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
    """Dependencies shared by the tools (injected, which makes testing easy)."""

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
        """Format expected by the Messages API `tools` parameter."""
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return {"name": self.name, "description": self.description, "input_schema": schema}


@dataclass
class ToolResult:
    content: str          # JSON sent back to the model
    is_error: bool = False
    data: dict = field(default_factory=dict)


def to_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=None, default=str)


class ToolExecutor:
    """Runs tool calls coming from the model, with validation and error handling.

    Errors never break the conversation: they become a `tool_result` with
    `is_error=True` and a clear message, so the model can fix its arguments or
    explain the situation to the customer.
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
            return ToolResult(to_json({"error": f"Unknown tool: {name}"}), is_error=True)
        try:
            params = tool.input_model.model_validate(arguments or {})
        except ValidationError as exc:
            problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in exc.errors())
            return ToolResult(to_json({"error": f"Invalid arguments: {problems}"}), is_error=True)
        try:
            data = tool.handler(self._context, params)
        except Exception as exc:  # noqa: BLE001 — any failure becomes a controlled response
            logger.exception("Tool %s failed", name)
            return ToolResult(to_json({"error": f"Internal error while querying the system: {exc}"}), is_error=True)
        return ToolResult(to_json(data), data=data)


# --------------------------------------------------------------------------- #
# Formatting (done in code so the model never gets math or formats wrong).
# Money and dates use Brazilian formats because they are shown to customers as is.
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
