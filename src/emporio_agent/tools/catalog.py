"""Ferramentas de catálogo: busca de produtos e detalhes de um produto.

Regras aplicadas aqui (e não no prompt), para que o modelo nunca precise "calcular":
  * disponibilidade (status + estoque) e o motivo da indisponibilidade;
  * preço promocional vigente x preço de tabela x preço no PIX (não cumulativos);
  * parcelamento máximo sem juros conforme o manual (§3.1);
  * alternativas semelhantes quando o produto está indisponível (§7.3).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from emporio_agent.text import normalize_text
from emporio_agent.tools.base import Tool, ToolContext, brl

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

_STOPWORDS = set("a o e de da do das dos um uma para com sem que tem voces vcs algum alguma".split())

_STATUS_LABEL = {
    "active": "ativo",
    "discontinued": "descontinuado (fora de catálogo)",
    "coming_soon": "em breve (lançamento ainda não disponível para venda)",
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _token_variants(token: str) -> set[str]:
    """Variações simples de singular/plural em português: violão/violões, pedal/pedais."""
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
    """Maior parcelamento sem juros permitido pelo manual (§3.1).

    Até 3x: parcela >= R$ 50,00 | 4x a 6x: parcela >= R$ 80,00 | 7x a 12x: parcela >= R$ 100,00.
    """
    for n in range(12, 0, -1):
        minimum = 100 if n >= 7 else 80 if n >= 4 else 50
        if n == 1 or price / n >= minimum:
            return {"parcelas": n, "valor_parcela": brl(round(price / n, 2))}
    return {"parcelas": 1, "valor_parcela": brl(price)}  # pragma: no cover


def availability_label(row: sqlite3.Row) -> str:
    if row["status"] == "discontinued":
        return "descontinuado"
    if row["status"] == "coming_soon":
        return "em breve"
    if row["stock_quantity"] <= 0:
        return "esgotado"
    if row["stock_quantity"] <= 2:
        return "últimas unidades"
    return "em estoque"


def _summary(row: sqlite3.Row) -> dict:
    """Resumo de um produto para listas de resultados."""
    item = {
        "produto_id": row["product_id"],
        "nome": row["name"],
        "categoria": row["category_name"],
        "disponibilidade": availability_label(row),
        "preco": brl(row["final_price_brl"]),
    }
    if row["promo_discount_percent"] is not None:
        item["promocao"] = {
            "nome": row["promo_description"],
            "desconto": f"{row['promo_discount_percent']:g}%",
            "preco_original": brl(row["price_brl"]),
        }
    else:
        item["preco_pix"] = brl(row["pix_price_brl"])
    return item


# --------------------------------------------------------------------------- #
# buscar_produtos
# --------------------------------------------------------------------------- #
class SearchProductsInput(BaseModel):
    termo: str | None = Field(
        default=None,
        description="Palavras-chave: marca, modelo ou característica (ex.: 'Yamaha', 'nylon', 'eletroacústico', "
        "'Takamine GD20'). Omitir para listar por categoria/preço.",
    )
    categoria: CategoryName | None = Field(default=None, description="Filtra por categoria do catálogo.")
    preco_min: float | None = Field(default=None, ge=0, description="Preço mínimo em reais (considera promoções).")
    preco_max: float | None = Field(default=None, ge=0, description="Preço máximo em reais (considera promoções).")
    apenas_disponiveis: bool = Field(
        default=True, description="Se true (padrão), retorna só produtos ativos e com estoque."
    )
    ordenar_por: Literal["menor_preco", "maior_preco"] = Field(default="menor_preco")
    limite: int = Field(default=8, ge=1, le=20, description="Máximo de produtos retornados.")

    @model_validator(mode="after")
    def _check(self) -> "SearchProductsInput":
        if not (self.termo or self.categoria or self.preco_min is not None or self.preco_max is not None):
            raise ValueError("informe ao menos um filtro: termo, categoria, preco_min ou preco_max")
        if self.preco_min is not None and self.preco_max is not None and self.preco_min > self.preco_max:
            raise ValueError("preco_min não pode ser maior que preco_max")
        return self


def search_products(ctx: ToolContext, params: SearchProductsInput) -> dict:
    base_where, base_args = [], []
    if params.categoria:
        base_where.append("category_name = ?")
        base_args.append(params.categoria)
    if params.preco_min is not None:
        base_where.append("final_price_brl >= ?")
        base_args.append(params.preco_min)
    if params.preco_max is not None:
        base_where.append("final_price_brl <= ?")
        base_args.append(params.preco_max)

    tokens = _search_tokens(params.termo)
    order = "final_price_brl ASC" if params.ordenar_por == "menor_preco" else "final_price_brl DESC"

    # Todas as palavras-chave precisam casar (AND). Não fazemos fallback para
    # correspondência parcial (OR): em testes, "cordas de guitarra" retornava
    # violões de 7 cordas — o que levaria o agente a oferecer o produto errado.
    # Sem resultados, o modelo é orientado a tentar termos mais simples.
    where, args = list(base_where), list(base_args)
    for token in tokens:
        clause, clause_args = _token_clause(token)
        where.append(clause)
        args.extend(clause_args)
    sql = "SELECT * FROM product_catalog" + (" WHERE " + " AND ".join(where) if where else "")
    rows = ctx.conn.execute(f"{sql} ORDER BY {order}", args).fetchall()

    available = [r for r in rows if r["is_available"]]
    unavailable = [r for r in rows if not r["is_available"]]
    selected = available if params.apenas_disponiveis else rows

    result: dict = {
        "total_encontrado": len(selected),
        "exibindo": min(len(selected), params.limite),
        "produtos": [_summary(r) for r in selected[: params.limite]],
    }
    if params.apenas_disponiveis and unavailable:
        result["indisponiveis_correspondentes"] = [
            {"produto_id": r["product_id"], "nome": r["name"], "situacao": availability_label(r)}
            for r in unavailable[:5]
        ]
    if not selected:
        result["aviso"] = (
            "Nenhum produto disponível com esses filtros. Se usou várias palavras-chave, tente termos mais simples "
            "(ex.: só a marca ou o tipo). Lembre-se: a loja vende apenas instrumentos musicais (não vende "
            "acessórios como cordas, cabos, palhetas, pedais, amplificadores ou cases)."
        )
    return result


# --------------------------------------------------------------------------- #
# detalhes_produto
# --------------------------------------------------------------------------- #
class ProductDetailsInput(BaseModel):
    produto_id: int | None = Field(default=None, description="ID do produto (obtido em buscar_produtos).")
    nome: str | None = Field(default=None, description="Nome ou parte do nome do produto, se o ID não for conhecido.")

    @model_validator(mode="after")
    def _check(self) -> "ProductDetailsInput":
        if self.produto_id is None and not (self.nome and self.nome.strip()):
            raise ValueError("informe produto_id ou nome")
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
    """Produtos disponíveis da mesma categoria, com preço mais próximo (manual §7.3)."""
    alternatives = conn.execute(
        "SELECT * FROM product_catalog WHERE is_available AND category_id = ? AND product_id <> ? "
        "ORDER BY ABS(final_price_brl - ?) LIMIT ?",
        (row["category_id"], row["product_id"], row["final_price_brl"], limit),
    ).fetchall()
    return [_summary(a) for a in alternatives]


def product_details(ctx: ToolContext, params: ProductDetailsInput) -> dict:
    if params.produto_id is not None:
        row = ctx.conn.execute("SELECT * FROM product_catalog WHERE product_id = ?", (params.produto_id,)).fetchone()
        matches = [row] if row else []
    else:
        matches = _find_by_name(ctx.conn, params.nome or "")

    if not matches:
        return {
            "encontrado": False,
            "mensagem": "Produto não encontrado no catálogo. Use buscar_produtos para procurar opções semelhantes.",
        }
    if len(matches) > 1:
        return {
            "encontrado": False,
            "mensagem": "Mais de um produto corresponde ao nome informado. Confirme com o cliente qual deles.",
            "candidatos": [_summary(r) for r in matches[:6]],
        }

    row = matches[0]
    details = _summary(row)
    details.update({
        "status_catalogo": _STATUS_LABEL.get(row["status"], row["status"]),
        "disponivel_para_venda": bool(row["is_available"]),
        "estoque": row["stock_quantity"] if row["is_available"] else 0,
        "descricao": row["description"],
        "especificacoes": json.loads(row["specs_json"] or "{}"),
        "preco_tabela": brl(row["price_brl"]),
        "parcelamento_maximo_sem_juros": installments(row["final_price_brl"]),
    })
    if row["promo_discount_percent"] is not None:
        details["observacao_pagamento"] = (
            "Produto em promoção: o desconto de 5% no PIX NÃO se aplica (promoções não são cumulativas)."
        )
    else:
        details["observacao_pagamento"] = "Pagamento no PIX tem 5% de desconto sobre o preço de tabela."
    if not row["is_available"]:
        details["alternativas_disponiveis"] = _alternatives(ctx.conn, row)
    return details


# --------------------------------------------------------------------------- #
# Registro
# --------------------------------------------------------------------------- #
SEARCH_PRODUCTS = Tool(
    name="buscar_produtos",
    description=(
        "Busca instrumentos no catálogo da loja, com preços, promoções vigentes e disponibilidade em tempo real. "
        "Use SEMPRE que o cliente perguntar sobre produtos, opções, faixas de preço, marcas ou se a loja tem algo. "
        "Aceita palavras-chave, categoria e faixa de preço. Por padrão retorna apenas itens disponíveis para venda; "
        "itens correspondentes porém indisponíveis aparecem em 'indisponiveis_correspondentes'."
    ),
    input_model=SearchProductsInput,
    handler=search_products,
)

PRODUCT_DETAILS = Tool(
    name="detalhes_produto",
    description=(
        "Retorna os detalhes completos de UM produto: preço de tabela, promoção vigente, preço no PIX, parcelamento "
        "máximo sem juros, estoque, especificações técnicas e, se estiver indisponível, alternativas semelhantes. "
        "Use quando o cliente perguntar o preço ou detalhes de um modelo específico, ou antes de confirmar "
        "disponibilidade ou condições de pagamento."
    ),
    input_model=ProductDetailsInput,
    handler=product_details,
)
