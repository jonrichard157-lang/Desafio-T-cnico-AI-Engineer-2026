"""Testes da configuração (data/hora simulada da loja)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, time

from emporio_agent.config import _parse_store_today, _split_list, get_settings


def test_store_today_accepts_date_or_datetime() -> None:
    assert _parse_store_today("") == (None, None)
    assert _parse_store_today("2026-02-20") == (date(2026, 2, 20), None)
    assert _parse_store_today("2026-02-20T10:30") == (date(2026, 2, 20), time(10, 30))


def test_now_uses_simulated_date_and_time_in_store_timezone() -> None:
    settings = replace(get_settings(), store_today=date(2026, 2, 20), store_time=time(10, 30))
    now = settings.now()
    assert (now.date(), now.hour, now.minute) == (date(2026, 2, 20), 10, 30)
    assert now.tzinfo is not None and settings.today() == date(2026, 2, 20)


def test_partner_stores_split() -> None:
    assert _split_list(" Loja A - (67) 1111-1111 ; ;Loja B ") == ("Loja A - (67) 1111-1111", "Loja B")
    assert _split_list("") == ()
