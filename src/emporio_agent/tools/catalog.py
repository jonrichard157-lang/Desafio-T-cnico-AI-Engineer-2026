"""Catalog tools: product search and details of a single product.

Rules applied here (not in the prompt), so the model never has to "calculate":
  * availability (status + stock) and the reason a product is unavailable;
  * current promotional price x list price x PIX price (they do not stack);
  * maximum interest-free installments according to the manual (section 3.1);
  * similar alternatives when a product is unavailable (section 7.3).

The catalog is in Portuguese, so category names and search keywords are Portuguese.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from emporio_agent.text import normalize_text
from emporio_agent.tools.base import Tool, ToolContext, brl

# Category names exactly as stored in the catalog (Portuguese).
CategoryName = Literal[
    "Guitarras",
    "Baixos",
    "Baterias e Percussão",
    "Teclados e Pianos",
    "Violões",
    "Instrumentos de Sopro (Madeiras)",
    "Instrumentos de Sopro (Metais)",
    "Cordas Orquestrais",
    "Ukuleles",
]

_CATEGORY_HELP = (
    "Catalog category (names are in Portuguese): 'Violões' = acoustic/classical guitars; 'Guitarras' = electric "
    "guitars; 'Baixos' = bass guitars; 'Baterias e Percussão' = drums and percussion; 'Teclados e Pianos' = "
    "keyboards and pianos; 'Instrumentos de Sopro (Madeiras)' = woodwinds; 'Instrumentos de Sopro (Metais)' = "
    "brass; 'Cordas Orquestrais' = orchestral strings; 'Ukuleles' = ukuleles."
)

# Portuguese stopwords, because customers' search terms are in Portuguese.
_STOPWORDS = set("a o e de da do das dos um uma para com sem que tem voces vcs algum alguma".split())

_STATUS_LABEL = {
    "active": "active",
    "discontinued": "discontinued (no longer in the catalog)",
    "coming_soon": "coming soon (launch not yet available for sale)",
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _token_variants(token: str) -> set[str]:
    """Simple Portuguese singular/plural variants: violão/violões, pedal/pedais."""
    variants = {token}
    if token.endswith("oes"):
        variants.add(token[:-3] + "ao")
    elif token.endswith("ao"):
        variants.add(token[:-2] + "oes")
    elif token.endswith("is"):
        variants.add(token[:-2] + "l")
    elif token.endswith("l"):
        variants.add(token[:-1] + "is")
    if token.endswith("s") and len(token) > 3:
        variants.add(token[:-1])
    return variants


def _search_tokens(term: str | None) -> list[str]:
    return [t for t in normalize_text(term).replace("-", " ").split() if t not in _STOPWORDS and len(t) > 1]


def _token_clause(token: str) -> tuple[str, list[str]]:
    variants = sorted(_token_variants(token))
    clause = "(" + " OR ".join("search_text LIKE ?" for _ in variants) + ")"
    return clause, [f"%{v}%" for v in variants]


def installments(price: float) -> dict:
    """Largest interest-free installment plan allowed by the manual (section 3.1).

    Up to 3x: installment >= R$ 50.00 | 4x to 6x: >= R$ 80.00 | 7x to 12x: >= R$ 100.00.
    """
    for n in range(12, 0, -1):
        minimum = 100 if n >= 7 else 80 if n >= 4 else 50
        if n == 1 or price / n >= minimum:
            return {"installments": n, "installment_value": brl(round(price / n, 2))}
    return {"installments": 1, "installment_value": brl(price)}  # pragma: no cover


def availability_label(row: sqlite3.Row) -> str:
    if row["status"] == "discontinued":
        return "discontinued"
    if row["status"] == "coming_soon":
        return "coming soon"
    if row["stock_quantity"] <= 0:
        return "out of stock"
    if row["stock_quantity"] <= 2:
        return "last units"
    return "in stock"


def _summary(row: sqlite3.Row) -> dict:
    """Short product summary used in result lists."""
    item = {
        "product_id": row["product_id"],
        "name": row["name"],
        "category": row["category_name"],
        "availability": availability_label(row),
        "price": brl(row["final_price_brl"]),
    }
    if row["promo_discount_percent"] is not None:
        item["promotion"] = {
            "name": row["promo_description"],
            "discount": f"{row['promo_discount_percent']:g}%",
            "original_price": brl(row["price_brl"]),
        }
    else:
        item["pix_price"] = brl(row["pix_price_brl"])
    return item


# --------------------------------------------------------------------------- #
# search_products
# --------------------------------------------------------------------------- #
class SearchProductsInput(BaseModel):
    query: str | None = Field(
        default=None,
        description="Keywords IN PORTUGUESE, as written in the catalog: brand, model or feature (e.g. 'Yamaha', "
        "'nylon', 'eletroacústico', 'Takamine GD20'). Omit to list by category/price only.",
    )
    category: CategoryName | None = Field(default=None, description=_CATEGORY_HELP)
    min_price: float | None = Field(default=None, ge=0, description="Minimum price in BRL (promotions included).")
    max_price: float | None = Field(default=None, ge=0, description="Maximum price in BRL (promotions included).")
    only_available: bool = Field(
        default=True, description="If true (default), returns only active products with stock."
    )
    sort_by: Literal["price_asc", "price_desc"] = Field(default="price_asc")
    limit: int = Field(default=8, ge=1, le=20, description="Maximum number of products returned.")

    @model_validator(mode="after")
    def _check(self) -> "SearchProductsInput":
        if not (self.query or self.category or self.min_price is not None or self.max_price is not None):
            raise ValueError("provide at least one filter: query, category, min_price or max_price")
        if self.min_price is not None and self.max_price is not None and self.min_price > self.max_price:
            raise ValueError("min_price cannot be greater than max_price")
        return self


def search_products(ctx: ToolContext, params: SearchProductsInput) -> dict:
    base_where, base_args = [], []
    if params.category:
        base_where.append("category_name = ?")
        base_args.append(params.category)
    if params.min_price is not None:
        base_where.append("final_price_brl >= ?")
        base_args.append(params.min_price)
    if params.max_price is not None:
        base_where.append("final_price_brl <= ?")
        base_args.append(params.max_price)

    tokens = _search_tokens(params.query)
    order = "final_price_brl ASC" if params.sort_by == "price_asc" else "final_price_brl DESC"

    # Every keyword must match (AND). There is no fallback to partial matching (OR):
    # in testing, "cordas de guitarra" (guitar strings) returned 7-string guitars,
    # which would lead the agent to offer the wrong product. With no results, the
    # model is told to try simpler terms.
    where, args = list(base_where), list(base_args)
    for token in tokens:
        clause, clause_args = _token_clause(token)
        where.append(clause)
        args.extend(clause_args)
    sql = "SELECT * FROM product_catalog" + (" WHERE " + " AND ".join(where) if where else "")
    rows = ctx.conn.execute(f"{sql} ORDER BY {order}", args).fetchall()

    available = [r for r in rows if r["is_available"]]
    unavailable = [r for r in rows if not r["is_available"]]
    selected = available if params.only_available else rows

    result: dict = {
        "total_found": len(selected),
        "showing": min(len(selected), params.limit),
        "products": [_summary(r) for r in selected[: params.limit]],
    }
    if params.only_available and unavailable:
        result["matching_unavailable"] = [
            {"product_id": r["product_id"], "name": r["name"], "situation": availability_label(r)}
            for r in unavailable[:5]
        ]
    if not selected:
        result["notice"] = (
            "No available product matches these filters. If you used several keywords, try simpler ones (e.g. only "
            "the brand or the type). Remember: the store sells musical instruments only (no accessories such as "
            "strings, cables, picks, pedals, amplifiers or cases)."
        )
    return result


# --------------------------------------------------------------------------- #
# get_product_details
# --------------------------------------------------------------------------- #
class ProductDetailsInput(BaseModel):
    product_id: int | None = Field(default=None, description="Product ID (from search_products).")
    name: str | None = Field(default=None, description="Product name or part of it, when the ID is not known.")

    @model_validator(mode="after")
    def _check(self) -> "ProductDetailsInput":
        if self.product_id is None and not (self.name and self.name.strip()):
            raise ValueError("provide product_id or name")
        return self


def _find_by_name(conn: sqlite3.Connection, name: str) -> list[sqlite3.Row]:
    rows = conn.execute("SELECT * FROM product_catalog").fetchall()
    needle = normalize_text(name)
    exact = [r for r in rows if needle and needle in normalize_text(r["name"])]
    if exact:
        return exact
    tokens = _search_tokens(name)
    if not tokens:
        return []
    return [r for r in rows if all(any(v in r["search_text"] for v in _token_variants(t)) for t in tokens)]


def _alternatives(conn: sqlite3.Connection, row: sqlite3.Row, limit: int = 3) -> list[dict]:
    """Available products of the same category with the closest price (manual section 7.3)."""
    alternatives = conn.execute(
        "SELECT * FROM product_catalog WHERE is_available AND category_id = ? AND product_id <> ? "
        "ORDER BY ABS(final_price_brl - ?) LIMIT ?",
        (row["category_id"], row["product_id"], row["final_price_brl"], limit),
    ).fetchall()
    return [_summary(a) for a in alternatives]


def product_details(ctx: ToolContext, params: ProductDetailsInput) -> dict:
    if params.product_id is not None:
        row = ctx.conn.execute("SELECT * FROM product_catalog WHERE product_id = ?", (params.product_id,)).fetchone()
        matches = [row] if row else []
    else:
        matches = _find_by_name(ctx.conn, params.name or "")

    if not matches:
        return {
            "found": False,
            "message": "Product not found in the catalog. Use search_products to look for similar options.",
        }
    if len(matches) > 1:
        return {
            "found": False,
            "message": "More than one product matches this name. Confirm with the customer which one they mean.",
            "candidates": [_summary(r) for r in matches[:6]],
        }

    row = matches[0]
    details = _summary(row)
    details.update({
        "catalog_status": _STATUS_LABEL.get(row["status"], row["status"]),
        "available_for_sale": bool(row["is_available"]),
        "stock": row["stock_quantity"] if row["is_available"] else 0,
        "description": row["description"],
        "specs": json.loads(row["specs_json"] or "{}"),
        "list_price": brl(row["price_brl"]),
        "max_interest_free_installments": installments(row["final_price_brl"]),
    })
    if row["promo_discount_percent"] is not None:
        details["payment_note"] = (
            "Product on promotion: the 5% PIX discount does NOT apply (promotions do not stack)."
        )
    else:
        details["payment_note"] = "Paying with PIX gives a 5% discount on the list price."
    if not row["is_available"]:
        details["available_alternatives"] = _alternatives(ctx.conn, row)
    return details


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #
SEARCH_PRODUCTS = Tool(
    name="search_products",
    description=(
        "Searches the store's catalog of instruments, with prices, current promotions and real-time availability. "
        "ALWAYS use it when the customer asks about products, options, price ranges, brands or whether the store "
        "has something. Accepts Portuguese keywords, a category and a price range. By default it returns only "
        "items available for sale; matching but unavailable items are listed in 'matching_unavailable'."
    ),
    input_model=SearchProductsInput,
    handler=search_products,
)

PRODUCT_DETAILS = Tool(
    name="get_product_details",
    description=(
        "Returns the full details of ONE product: list price, current promotion, PIX price, maximum interest-free "
        "installments, stock, technical specs and, if unavailable, similar alternatives. Use it when the customer "
        "asks about the price or details of a specific model, or before confirming availability or payment terms."
    ),
    input_model=ProductDetailsInput,
    handler=product_details,
)
