"""Configuração central do projeto, lida a partir de variáveis de ambiente / `.env`."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve(path_str: str) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str
    anthropic_model: str
    anthropic_max_tokens: int
    data_dir: Path
    storage_dir: Path
    store_timezone: str
    store_today: date | None
    api_host: str
    api_port: int

    @property
    def db_path(self) -> Path:
        """Banco SQLite com os dados operacionais (produtos, pedidos, ...)."""
        return self.storage_dir / "emporio.db"

    @property
    def sessions_db_path(self) -> Path:
        """Banco SQLite com o histórico de conversas por sessão."""
        return self.storage_dir / "sessions.db"

    @property
    def policies_pdf(self) -> Path:
        return self.data_dir / "políticas_da_loja.pdf"

    def now(self) -> datetime:
        """Data/hora atual no fuso da loja (respeita STORE_TODAY, se definido)."""
        tz = ZoneInfo(self.store_timezone)
        current = datetime.now(tz)
        if self.store_today:
            current = current.replace(
                year=self.store_today.year,
                month=self.store_today.month,
                day=self.store_today.day,
            )
        return current

    def today(self) -> date:
        return self.now().date()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    today_raw = os.getenv("STORE_TODAY", "").strip()
    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
        anthropic_max_tokens=int(os.getenv("ANTHROPIC_MAX_TOKENS", "1024")),
        data_dir=_resolve(os.getenv("DATA_DIR", "data")),
        storage_dir=_resolve(os.getenv("STORAGE_DIR", "storage")),
        store_timezone=os.getenv("STORE_TIMEZONE", "America/Campo_Grande"),
        store_today=date.fromisoformat(today_raw) if today_raw else None,
        api_host=os.getenv("API_HOST", "127.0.0.1"),
        api_port=int(os.getenv("API_PORT", "8000")),
    )

