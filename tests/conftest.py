"""Fixtures compartilhadas pelos testes."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from emporio_agent.config import PROJECT_ROOT, get_settings
from emporio_agent.data.loader import build_database, connect_readonly
from emporio_agent.policies.manual import load_manual
from emporio_agent.tools import ALL_TOOLS, ToolContext, ToolExecutor


@pytest.fixture(scope="session")
def db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Banco construído a partir dos CSVs reais, num diretório temporário."""
    path = tmp_path_factory.mktemp("db") / "emporio.db"
    build_database(PROJECT_ROOT / "data", path)
    return path


@pytest.fixture()
def conn(db_path: Path) -> sqlite3.Connection:
    connection = connect_readonly(db_path)
    yield connection
    connection.close()


@pytest.fixture()
def executor(conn) -> ToolExecutor:
    context = ToolContext(conn=conn, manual=load_manual(), today=lambda: date(2026, 3, 10))
    return ToolExecutor(ALL_TOOLS, context)


@pytest.fixture()
def settings():
    return replace(get_settings(), anthropic_model="modelo-teste", partner_stores=("Loja Parceira X",))
