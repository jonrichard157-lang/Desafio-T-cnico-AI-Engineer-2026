"""Carga dos CSVs fornecidos pela loja em um banco SQLite tipado.

Por que SQLite?
  * Os dados são pequenos (dezenas de linhas), relacionais e precisam de filtros
    exatos (preço máximo, estoque > 0, status do pedido). SQL resolve isso de forma
    determinística, sem depender do modelo para "ler" tabelas.
  * Faz parte da biblioteca padrão do Python: zero infraestrutura extra.
  * As regras de preço/disponibilidade ficam centralizadas na view `product_catalog`.

Uso:
    uv run python -m emporio_agent.data.loader          # (re)constrói o banco e mostra um resumo
"""

from __future__ import annotations

import csv
import json
import logging
import os
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Iterable

from emporio_agent.config import get_settings
from emporio_agent.text import normalize_text, only_digits

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
CSV_PREFIX = "desafio_tecnico_ai_eng - "
TABLES = ("categories", "products", "promotions", "customers", "orders", "order_items")


# --------------------------------------------------------------------------- #
# Leitura e conversão
# --------------------------------------------------------------------------- #
def _csv_path(data_dir: Path, table: str) -> Path:
    return data_dir / f"{CSV_PREFIX}{table}.csv"


def _read_csv(data_dir: Path, table: str) -> list[dict[str, str]]:
    # utf-8-sig tolera BOM caso os arquivos sejam reexportados pelo Excel.
    with _csv_path(data_dir, table).open(encoding="utf-8-sig", newline="") as fh:
        return [{k.strip(): (v or "").strip() for k, v in row.items()} for row in csv.DictReader(fh)]


def _none_if_empty(value: str) -> str | None:
    return value or None


def _parse_specs(raw: str, product_id: str, issues: list[tuple]) -> dict:
    if not raw:
        return {}
    try:
        specs = json.loads(raw)
        return specs if isinstance(specs, dict) else {}
    except json.JSONDecodeError:
        issues.append(("products", product_id, "campo specs com JSON inválido; ignorado"))
        return {}


# --------------------------------------------------------------------------- #
# Construção do banco
# --------------------------------------------------------------------------- #
def build_database(data_dir: Path, db_path: Path) -> list[tuple[str, str, str]]:
    """Cria o banco do zero a partir dos CSVs. Retorna os problemas de qualidade encontrados.

    A escrita é feita num arquivo temporário e depois movida, para que um leitor
    nunca veja um banco pela metade.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_suffix(".tmp")
    tmp_path.unlink(missing_ok=True)

    raw = {table: _read_csv(data_dir, table) for table in TABLES}
    issues: list[tuple[str, str, str]] = []

    conn = sqlite3.connect(tmp_path)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        categories = {r["category_id"]: r["name"] for r in raw["categories"]}

        conn.executemany(
            "INSERT INTO categories VALUES (?, ?, ?)",
            [(int(r["category_id"]), r["name"], _none_if_empty(r["description"])) for r in raw["categories"]],
        )

        product_rows = []
        for r in raw["products"]:
            specs = _parse_specs(r["specs"], r["product_id"], issues)
            search_text = normalize_text(
                " ".join([r["name"], categories.get(r["category_id"], ""), r["description"], *map(str, specs.values())])
            )
            product_rows.append((
                int(r["product_id"]), r["name"], int(r["category_id"]), float(r["price_brl"]),
                int(r["stock_quantity"]), r["status"], _none_if_empty(r["description"]),
                json.dumps(specs, ensure_ascii=False), _none_if_empty(r["created_at"]), search_text,
            ))
        conn.executemany(
            "INSERT INTO products (product_id, name, category_id, price_brl, stock_quantity, status,"
            " description, specs_json, created_at, search_text) VALUES (?,?,?,?,?,?,?,?,?,?)",
            product_rows,
        )

        conn.executemany(
            "INSERT INTO promotions VALUES (?, ?, ?, ?, ?)",
            [(int(r["promotion_id"]), int(r["product_id"]), float(r["discount_percent"]),
              _none_if_empty(r["description"]), int(r["is_active"])) for r in raw["promotions"]],
        )

        conn.executemany(
            "INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(int(r["customer_id"]), r["name"], _none_if_empty(r["phone"]), only_digits(r["phone"]),
              _none_if_empty(r["email"]), r["email"].lower() or None, _none_if_empty(r["city"]))
             for r in raw["customers"]],
        )

        conn.executemany(
            "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(int(r["order_id"]), int(r["customer_id"]), r["order_date"], r["status"], float(r["total_brl"]),
              _none_if_empty(r["payment_method"]), _none_if_empty(r["tracking_code"]),
              _none_if_empty(r["estimated_delivery"]), _none_if_empty(r["notes"])) for r in raw["orders"]],
        )

        conn.executemany(
            "INSERT INTO order_items VALUES (?, ?, ?)",
            [(int(r["order_id"]), int(r["product_id"]), int(r["quantity"])) for r in raw["order_items"]],
        )

        issues.extend(_quality_checks(conn))
        conn.executemany("INSERT INTO data_quality_issues VALUES (?, ?, ?)", issues)

        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise ValueError(f"Violação de chave estrangeira nos CSVs: {fk_errors}")
        conn.commit()
    finally:
        conn.close()

    os.replace(tmp_path, db_path)
    for entity, entity_id, issue in issues:
        logger.info("Qualidade de dados [%s %s]: %s", entity, entity_id, issue)
    return issues


def _quality_checks(conn: sqlite3.Connection) -> Iterable[tuple[str, str, str]]:
    """Inconsistências encontradas nos dados. Não bloqueiam a carga, mas são registradas
    e tratadas explicitamente pelas regras do agente (ver README)."""
    q = conn.execute

    for pid, name in q("SELECT product_id, name FROM products WHERE status='active' AND stock_quantity=0"):
        yield ("products", str(pid), f"'{name}' está ativo mas sem estoque -> tratado como indisponível")
    for pid, name, stock in q("SELECT product_id, name, stock_quantity FROM products "
                              "WHERE status IN ('discontinued','coming_soon') AND stock_quantity>0"):
        yield ("products", str(pid), f"'{name}' não está ativo mas tem estoque={stock} -> tratado como indisponível")

    for pid, n in q("SELECT product_id, COUNT(*) FROM promotions WHERE is_active=1 "
                    "GROUP BY product_id HAVING COUNT(*)>1"):
        yield ("promotions", str(pid), f"{n} promoções ativas simultâneas -> considera-se o maior desconto")

    for oid, total, items_total in q(
        "SELECT o.order_id, o.total_brl, SUM(p.price_brl * oi.quantity) FROM orders o "
        "JOIN order_items oi ON oi.order_id=o.order_id JOIN products p ON p.product_id=oi.product_id "
        "GROUP BY o.order_id HAVING ABS(o.total_brl - SUM(p.price_brl * oi.quantity)) > 0.01"
    ):
        yield ("orders", str(oid), f"total do pedido (R$ {total:.2f}) difere da soma dos itens a preço atual "
                                   f"(R$ {items_total:.2f}) -> prevalece o total registrado no pedido")

    for oid, status in q("SELECT order_id, status FROM orders WHERE status IN ('shipped','delivered') "
                         "AND tracking_code IS NULL"):
        yield ("orders", str(oid), f"pedido '{status}' sem código de rastreio")

    emails = Counter(row[0] for row in q("SELECT email_norm FROM customers WHERE email_norm IS NOT NULL"))
    for email, n in emails.items():
        if n > 1:
            yield ("customers", email, f"e-mail compartilhado por {n} clientes -> e-mail sozinho não identifica o cliente")


# --------------------------------------------------------------------------- #
# Acesso
# --------------------------------------------------------------------------- #
def _is_stale(data_dir: Path, db_path: Path) -> bool:
    if not db_path.exists():
        return True
    db_mtime = db_path.stat().st_mtime
    sources = [_csv_path(data_dir, t) for t in TABLES] + [SCHEMA_PATH]
    return any(src.stat().st_mtime > db_mtime for src in sources)


def ensure_database(data_dir: Path | None = None, db_path: Path | None = None) -> Path:
    """Garante que o banco existe e está atualizado em relação aos CSVs."""
    settings = get_settings()
    data_dir = data_dir or settings.data_dir
    db_path = db_path or settings.db_path
    if _is_stale(data_dir, db_path):
        logger.info("Construindo banco de dados em %s", db_path)
        build_database(data_dir, db_path)
    return db_path


def connect_readonly(db_path: Path | None = None) -> sqlite3.Connection:
    """Conexão SOMENTE LEITURA: as ferramentas do agente nunca alteram dados."""
    db_path = ensure_database(db_path=db_path) if db_path is None else db_path
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    settings = get_settings()
    issues = build_database(settings.data_dir, settings.db_path)
    with connect_readonly(settings.db_path) as conn:
        print(f"Banco criado em: {settings.db_path}")
        for table in TABLES:
            print(f"  {table:<12} {conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]:>4} linhas")
        available = conn.execute("SELECT COUNT(*) FROM product_catalog WHERE is_available").fetchone()[0]
        print(f"  produtos disponíveis para venda: {available}")
    print(f"\nProblemas de qualidade registrados ({len(issues)}):")
    for entity, entity_id, issue in issues:
        print(f"  - [{entity} {entity_id}] {issue}")


if __name__ == "__main__":
    main()

