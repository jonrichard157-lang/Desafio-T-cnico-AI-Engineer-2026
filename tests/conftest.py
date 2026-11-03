"""Fixtures compartilhadas pelos testes."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from emporio_agent.config import PROJECT_ROOT
from emporio_agent.data.loader import build_database, connect_readonly


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

