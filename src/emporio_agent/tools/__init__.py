"""Ferramentas disponíveis para o agente.

`build_executor()` monta o executor com as dependências reais (banco + manual).
"""

from __future__ import annotations

from emporio_agent.config import get_settings
from emporio_agent.data.loader import connect_readonly
from emporio_agent.policies.manual import load_manual
from emporio_agent.tools.base import Tool, ToolContext, ToolExecutor, ToolResult
from emporio_agent.tools.catalog import PRODUCT_DETAILS, SEARCH_PRODUCTS
from emporio_agent.tools.orders import LOOKUP_ORDERS
from emporio_agent.tools.policies import LOOKUP_POLICY

ALL_TOOLS: list[Tool] = [SEARCH_PRODUCTS, PRODUCT_DETAILS, LOOKUP_ORDERS, LOOKUP_POLICY]


def build_executor() -> ToolExecutor:
    settings = get_settings()
    context = ToolContext(conn=connect_readonly(), manual=load_manual(), today=settings.today)
    return ToolExecutor(ALL_TOOLS, context)


__all__ = ["ALL_TOOLS", "Tool", "ToolContext", "ToolExecutor", "ToolResult", "build_executor"]
