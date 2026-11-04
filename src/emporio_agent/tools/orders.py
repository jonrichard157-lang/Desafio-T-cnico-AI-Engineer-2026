"""Ferramenta de consulta de pedidos, com verificação de identidade e prazos pós-venda.

Privacidade (LGPD, manual §9):
  * O pedido só é exibido se o cliente informar um dado que confira com o cadastro
    (telefone ou e-mail). Sem o número do pedido, exige-se telefone E e-mail.
  * Em caso de falha, a resposta é genérica: não revela se o pedido existe.
  * A resposta traz apenas o primeiro nome do cliente, nunca telefone/e-mail.

Prazos (manual §4 e §8), calculados no código para evitar erros de data do modelo:
  * Arrependimento e troca por preferência: até 7 dias corridos após o RECEBIMENTO.
  * Troca por defeito: até 30 dias corridos após a COMPRA.
  * Garantia legal: 90 dias após o recebimento.
  Suposição documentada: os dados não têm a data real de entrega; para pedidos
  entregues usamos `estimated_delivery` como data de recebimento.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from pydantic import BaseModel, Field, model_validator

from emporio_agent.text import only_digits
from emporio_agent.tools.base import Tool, ToolContext, br_date, brl

# Suposição documentada no README sobre o significado de cada status.
ORDER_STATUS = {
    "pending": "Pendente — pedido recebido, aguardando confirmação do pagamento",
    "confirmed": "Confirmado — pagamento aprovado, pedido em separação para envio",
    "shipped": "Enviado — pedido despachado, em trânsito",
    "delivered": "Entregue",
    "cancelled": "Cancelado",
}

PAYMENT_METHODS = {
    "pix": "PIX",
    "boleto": "Boleto bancário",
    "debit": "Cartão de débito",
}

_NOT_VERIFIED = {
    "verificado": False,
    "mensagem": (
        "Não foi possível localizar um pedido com os dados informados. Peça ao cliente para conferir o número "
        "do pedido e o telefone ou e-mail usados na compra. Não informe se o pedido existe ou não."
    ),
}


def _payment_label(method: str | None) -> str | None:
    if not method:
        return None
    if method.startswith("credit_"):
        return f"Cartão de crédito em {method.removeprefix('credit_')}"
    return PAYMENT_METHODS.get(method, method)


def _phone_matches(stored_digits: str | None, informed: str) -> bool:
    digits = only_digits(informed)
    # Aceita com ou sem DDD/código do país, desde que haja ao menos 8 dígitos.
    if len(digits) < 8 or not stored_digits:
        return False
    return stored_digits.endswith(digits[-11:]) or digits.endswith(stored_digits)


def _identity_matches(customer: sqlite3.Row, phone: str | None, email: str | None) -> bool:
    phone_ok = bool(phone) and _phone_matches(customer["phone_digits"], phone)
    email_ok = bool(email) and (customer["email_norm"] or "") == email.strip().lower()
    return phone_ok or email_ok


def _deadline(limit: date, today: date) -> dict:
    remaining = (limit - today).days
    return {
        "prazo_final": br_date(limit),
        "dentro_do_prazo": remaining >= 0,
        "dias_restantes": max(remaining, 0),
    }


def after_sales_deadlines(order: sqlite3.Row, today: date) -> dict:
    """Situação de cada direito pós-venda para o pedido, na data de hoje."""
    status = order["status"]
    order_date = date.fromisoformat(order["order_date"])

    if status == "cancelled":
        return {"observacao": "Pedido cancelado: não há trocas, devoluções ou garantia aplicáveis."}
    if status in ("pending", "confirmed", "shipped"):
        return {
            "observacao": (
                "O pedido ainda não foi recebido pelo cliente. Os prazos de arrependimento e de troca por "
                "preferência (7 dias) e a garantia legal (90 dias) começam a contar a partir do recebimento. "
                "Para cancelar antes do recebimento, encaminhar à equipe de atendimento humano (o manual não "
                "define procedimento de cancelamento)."
            ),
        }

    received_raw = order["estimated_delivery"]
    received = date.fromisoformat(received_raw) if received_raw else order_date
    return {
        "data_recebimento_considerada": br_date(received),
        "suposicao": "Data de recebimento estimada (o sistema não registra a data real de entrega).",
        "arrependimento_7_dias_do_recebimento": _deadline(received + timedelta(days=7), today),
        "troca_por_preferencia_7_dias_do_recebimento": _deadline(received + timedelta(days=7), today),
        "troca_por_defeito_30_dias_da_compra": _deadline(order_date + timedelta(days=30), today),
        "garantia_legal_90_dias_do_recebimento": _deadline(received + timedelta(days=90), today),
        "apos_garantia_legal": "Acionar a garantia do fabricante (6 meses a 2 anos); a loja pode intermediar.",
    }


def _order_payload(conn: sqlite3.Connection, order: sqlite3.Row, customer: sqlite3.Row, today: date) -> dict:
    items = conn.execute(
        "SELECT oi.quantity, p.name, p.product_id FROM order_items oi "
        "JOIN products p ON p.product_id = oi.product_id WHERE oi.order_id = ?",
        (order["order_id"],),
    ).fetchall()
    payload = {
        "numero_pedido": order["order_id"],
        "cliente_primeiro_nome": customer["name"].split()[0],
        "data_pedido": br_date(order["order_date"]),
        "status": ORDER_STATUS.get(order["status"], order["status"]),
        "itens": [{"produto": i["name"], "quantidade": i["quantity"]} for i in items],
        "total": brl(order["total_brl"]),
        "forma_pagamento": _payment_label(order["payment_method"]),
        "codigo_rastreio": order["tracking_code"],
        "previsao_entrega": br_date(order["estimated_delivery"]),
        "observacoes": order["notes"],
        "prazos_pos_venda": after_sales_deadlines(order, today),
    }
    if order["tracking_code"]:
        payload["como_rastrear"] = "Consultar o código no site dos Correios ou da Jadlog."
    if order["status"] == "shipped" and order["estimated_delivery"] and date.fromisoformat(order["estimated_delivery"]) < today:
        payload["alerta"] = (
            "A previsão de entrega já passou e o pedido ainda consta como enviado. Peça desculpas pelo transtorno "
            "e ofereça encaminhar o caso à equipe para verificar com a transportadora (retorno em até 24h úteis)."
        )
    return {k: v for k, v in payload.items() if v is not None}


class OrderLookupInput(BaseModel):
    numero_pedido: int | None = Field(default=None, description="Número do pedido informado pelo cliente.")
    telefone: str | None = Field(default=None, description="Telefone usado na compra (qualquer formato).")
    email: str | None = Field(default=None, description="E-mail usado na compra.")

    @model_validator(mode="after")
    def _check(self) -> "OrderLookupInput":
        if self.numero_pedido is not None:
            if not (self.telefone or self.email):
                raise ValueError("para consultar pelo número do pedido, informe também telefone ou e-mail do cliente")
        elif not (self.telefone and self.email):
            raise ValueError("sem o número do pedido, informe telefone E e-mail do cliente")
        return self


def lookup_orders(ctx: ToolContext, params: OrderLookupInput) -> dict:
    today = ctx.today()
    conn = ctx.conn

    if params.numero_pedido is not None:
        order = conn.execute("SELECT * FROM orders WHERE order_id = ?", (params.numero_pedido,)).fetchone()
        if order is None:
            return _NOT_VERIFIED
        customer = conn.execute("SELECT * FROM customers WHERE customer_id = ?", (order["customer_id"],)).fetchone()
        if customer is None or not _identity_matches(customer, params.telefone, params.email):
            return _NOT_VERIFIED
        return {"verificado": True, "data_de_hoje": br_date(today), "pedidos": [_order_payload(conn, order, customer, today)]}

    # Sem número do pedido: exige telefone E e-mail do mesmo cadastro.
    candidates = conn.execute(
        "SELECT * FROM customers WHERE email_norm = ?", ((params.email or "").strip().lower(),)
    ).fetchall()
    customer = next((c for c in candidates if _phone_matches(c["phone_digits"], params.telefone or "")), None)
    if customer is None:
        return _NOT_VERIFIED
    orders = conn.execute(
        "SELECT * FROM orders WHERE customer_id = ? ORDER BY order_date DESC", (customer["customer_id"],)
    ).fetchall()
    if not orders:
        return {"verificado": True, "mensagem": "Cliente identificado, mas não há pedidos registrados.", "pedidos": []}
    return {
        "verificado": True,
        "data_de_hoje": br_date(today),
        "pedidos": [_order_payload(conn, o, customer, today) for o in orders],
    }


LOOKUP_ORDERS = Tool(
    name="consultar_pedido",
    description=(
        "Consulta pedidos do cliente: status, itens, total, forma de pagamento, código de rastreio, previsão de "
        "entrega e a situação dos prazos de arrependimento, troca e garantia na data de hoje. "
        "Exige verificação de identidade: número do pedido + telefone OU e-mail; ou, sem o número, telefone E e-mail. "
        "Se o cliente não informou esses dados, PEÇA-OS antes de chamar esta ferramenta. "
        "Use para perguntas sobre status, entrega, rastreio, devolução, troca ou garantia de uma compra."
    ),
    input_model=OrderLookupInput,
    handler=lookup_orders,
)
