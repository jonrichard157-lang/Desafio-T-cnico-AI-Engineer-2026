"""Tests of the CSV -> SQLite load and of the `product_catalog` view business rules."""

from __future__ import annotations

import sqlite3

import pytest

from emporio_agent.text import normalize_text, only_digits


def test_row_counts(conn: sqlite3.Connection) -> None:
    expected = {"categories": 9, "products": 65, "promotions": 25, "customers": 50, "orders": 20, "order_items": 22}
    for table, count in expected.items():
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == count


def test_connection_is_read_only(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM products")


def test_types_are_converted(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT price_brl, stock_quantity FROM products WHERE product_id = 81").fetchone()
    assert isinstance(row["price_brl"], float)
    assert isinstance(row["stock_quantity"], int)


def test_takamine_gd20_price_and_pix(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT * FROM product_catalog WHERE name LIKE 'Takamine GD20%'").fetchone()
    assert row["price_brl"] == 2199
    assert row["is_available"] == 1
    assert row["promo_discount_percent"] is None
    assert row["pix_price_brl"] == pytest.approx(2089.05)


def test_promotion_is_not_cumulative_with_pix(conn: sqlite3.Connection) -> None:
    """Manual 6.2: with an active promotion there is no extra PIX discount."""
    row = conn.execute("SELECT * FROM product_catalog WHERE product_id = 121").fetchone()
    assert row["promo_discount_percent"] == 20
    assert row["final_price_brl"] == pytest.approx(round(row["price_brl"] * 0.8, 2))
    assert row["pix_price_brl"] is None


def test_inactive_promotions_are_ignored(conn: sqlite3.Connection) -> None:
    # Product 102 only has an inactive promotion (Black Friday, is_active=0).
    row = conn.execute("SELECT * FROM product_catalog WHERE product_id = 102").fetchone()
    assert row["promo_discount_percent"] is None
    assert row["final_price_brl"] == row["price_brl"]


@pytest.mark.parametrize(
    ("product_id", "reason"),
    [(96, "active without stock"), (113, "discontinued"), (130, "coming soon"), (136, "discontinued with stock")],
)
def test_unavailable_products(conn: sqlite3.Connection, product_id: int, reason: str) -> None:
    row = conn.execute("SELECT is_available FROM product_catalog WHERE product_id = ?", (product_id,)).fetchone()
    assert row["is_available"] == 0, reason


def test_search_text_is_normalized(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT search_text FROM products WHERE product_id = 81").fetchone()
    assert "violoes" in row["search_text"]  # category without accents
    assert "spruce" in row["search_text"]   # value coming from specs


def test_customer_lookup_fields(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT * FROM customers WHERE phone_digits = '67998123456'").fetchone()
    assert row["name"] == "Lucas Mendes da Silva"


def test_quality_issues_are_recorded(conn: sqlite3.Connection) -> None:
    issues = {(r["entity"], r["entity_id"]) for r in conn.execute("SELECT * FROM data_quality_issues")}
    assert ("products", "96") in issues
    assert ("orders", "3") in issues
    assert ("customers", "bruno.martins@jmail.com") in issues


def test_text_helpers() -> None:
    assert normalize_text("  Violão   CLÁSSICO ") == "violao classico"
    assert only_digits("(67) 99812-3456") == "67998123456"

