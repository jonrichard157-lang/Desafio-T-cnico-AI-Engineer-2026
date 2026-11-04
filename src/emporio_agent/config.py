"""Configuração central do projeto, lida a partir de variáveis de ambiente / `.env`."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, time
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
    store_time: time | None
    partner_stores: tuple[str, ...]

    @property
    def db_path(self) -> Path:
        """Banco SQLite com os dados operacionais (produtos, pedidos, ...)."""
        return self.storage_dir / "emporio.db"

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
        if self.store_time:
            current = current.replace(hour=self.store_time.hour, minute=self.store_time.minute,
                                      second=0, microsecond=0)
        return current

    def today(self) -> date:
        return self.now().date()


def _parse_store_today(raw: str) -> tuple[date | None, time | None]:
    """'2026-02-20' -> (data, None); '2026-02-20T10:30' -> (data, hora)."""
    raw = raw.strip()
    if not raw:
        return None, None
    if "T" in raw or " " in raw:
        moment = datetime.fromisoformat(raw)
        return moment.date(), moment.time()
    return date.fromisoformat(raw), None


def _split_list(raw: str) -> tuple[str, ...]:
    """'Loja A - (67) 1111-1111; Loja B' -> ('Loja A - (67) 1111-1111', 'Loja B')"""
    return tuple(item.strip() for item in raw.split(";") if item.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    store_today, store_time = _parse_store_today(os.getenv("STORE_TODAY", ""))
    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        anthropic_max_tokens=int(os.getenv("ANTHROPIC_MAX_TOKENS", "1024")),
        data_dir=_resolve(os.getenv("DATA_DIR", "data")),
        storage_dir=_resolve(os.getenv("STORAGE_DIR", "storage")),
        store_timezone=os.getenv("STORE_TIMEZONE", "America/Campo_Grande"),
        store_today=store_today,
        store_time=store_time,
        partner_stores=_split_list(os.getenv("PARTNER_STORES", "")),
    )

