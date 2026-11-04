"""Order lookup tool, with identity verification and after-sales deadlines.

Privacy (LGPD, manual section 9):
  * An order is only shown if the customer provides data that matches the record
    (phone or e-mail). Without the order number, phone AND e-mail are required.
  * On failure the answer is generic: it does not reveal whether the order exists.
  * Only the customer's first name is returned, never their phone or e-mail.

Deadlines (manual sections 4 and 8), computed in code to avoid date mistakes by the model:
  * Right of withdrawal (regret) and exchange by preference: up to 7 calendar days after RECEIPT.
  * Exchange for a defect: up to 30 calendar days after PURCHASE.
  * Legal warranty: 90 days after receipt.
  Documented assumption: the data has no actual delivery date; for delivered orders
  `estimated_delivery` is used as the date of receipt.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from pydantic import BaseModel, Field, model_validator

from emporio_agent.text import only_digits
from emporio_agent.tools.base import Tool, ToolContext, br_date, brl

# Assumed meaning of each status (documented in the README).
ORDER_STATUS = {
    "pending": "Pending — order received, waiting for payment confirmation",
    "confirmed": "Confirmed — payment approved, order being prepared for shipping",
    "shipped": "Shipped — order dispatched, in transit",
    "delivered": "Delivered",
    "cancelled": "Cancelled",
}

PAYMENT_METHODS = {
    "pix": "PIX",
    "boleto": "Boleto (bank slip)",
    "debit": "Debit card",
}

_NOT_VERIFIED = {
    "verified": False,
    "message": (
        "No order could be found with the data provided. Ask the customer to double-check the order number and "
        "the phone or e-mail used for the purchase. Do not say whether the order exists or not."
    ),
}


def _payment_label(method: str | None) -> str | None:
    if not method:
        return None
    if method.startswith("credit_"):
        return f"Credit card in {method.removeprefix('credit_')}"
    return PAYMENT_METHODS.get(method, method)


def _phone_matches(stored_digits: str | None, informed: str) -> bool:
    digits = only_digits(informed)
    # Accepts the number with or without area/country code, as long as it has at least 8 digits.
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
        "deadline": br_date(limit),
        "within_deadline": remaining >= 0,
        "days_left": max(remaining, 0),
    }


def after_sales_deadlines(order: sqlite3.Row, today: date) -> dict:
    """Status of each after-sales right for the order, as of today."""
    status = order["status"]
    order_date = date.fromisoformat(order["order_date"])

    if status == "cancelled":
        return {"note": "Cancelled order: no exchanges, returns or warranty apply."}
    if status in ("pending", "confirmed", "shipped"):
        return {
            "note": (
                "The customer has not received the order yet. The regret and exchange-by-preference periods "
                "(7 days) and the legal warranty (90 days) start counting from receipt. To cancel before receipt, "
                "refer the customer to the human service team (the manual defines no cancellation procedure)."
            ),
        }

    received_raw = order["estimated_delivery"]
    received = date.fromisoformat(received_raw) if received_raw else order_date
    return {
        "assumed_receipt_date": br_date(received),
        "assumption": "Estimated receipt date (the system does not record the actual delivery date).",
        "regret_7_days_after_receipt": _deadline(received + timedelta(days=7), today),
        "exchange_by_preference_7_days_after_receipt": _deadline(received + timedelta(days=7), today),
        "exchange_for_defect_30_days_after_purchase": _deadline(order_date + timedelta(days=30), today),
        "legal_warranty_90_days_after_receipt": _deadline(received + timedelta(days=90), today),
        "after_legal_warranty": "Use the manufacturer's warranty (6 months to 2 years); the store can mediate.",
    }


def _order_payload(conn: sqlite3.Connection, order: sqlite3.Row, customer: sqlite3.Row, today: date) -> dict:
    items = conn.execute(
        "SELECT oi.quantity, p.name, p.product_id FROM order_items oi "
        "JOIN products p ON p.product_id = oi.product_id WHERE oi.order_id = ?",
        (order["order_id"],),
    ).fetchall()
    payload = {
        "order_id": order["order_id"],
        "customer_first_name": customer["name"].split()[0],
        "order_date": br_date(order["order_date"]),
        "status": ORDER_STATUS.get(order["status"], order["status"]),
        "items": [{"product": i["name"], "quantity": i["quantity"]} for i in items],
        "total": brl(order["total_brl"]),
        "payment_method": _payment_label(order["payment_method"]),
        "tracking_code": order["tracking_code"],
        "estimated_delivery": br_date(order["estimated_delivery"]),
        "notes": order["notes"],
        "after_sales_deadlines": after_sales_deadlines(order, today),
    }
    if order["tracking_code"]:
        payload["how_to_track"] = "Look up the code on the Correios or Jadlog website."
    if order["status"] == "shipped" and order["estimated_delivery"] and date.fromisoformat(order["estimated_delivery"]) < today:
        payload["alert"] = (
            "The estimated delivery date has passed and the order is still marked as shipped. Apologize for the "
            "inconvenience and offer to forward the case to the team to check with the carrier (reply within "
            "24 business hours)."
        )
    return {k: v for k, v in payload.items() if v is not None}


class OrderLookupInput(BaseModel):
    order_id: int | None = Field(default=None, description="Order number given by the customer.")
    phone: str | None = Field(default=None, description="Phone number used for the purchase (any format).")
    email: str | None = Field(default=None, description="E-mail used for the purchase.")

    @model_validator(mode="after")
    def _check(self) -> "OrderLookupInput":
        if self.order_id is not None:
            if not (self.phone or self.email):
                raise ValueError("to look up by order number, also provide the customer's phone or e-mail")
        elif not (self.phone and self.email):
            raise ValueError("without the order number, provide the customer's phone AND e-mail")
        return self


def lookup_orders(ctx: ToolContext, params: OrderLookupInput) -> dict:
    today = ctx.today()
    conn = ctx.conn

    if params.order_id is not None:
        order = conn.execute("SELECT * FROM orders WHERE order_id = ?", (params.order_id,)).fetchone()
        if order is None:
            return _NOT_VERIFIED
        customer = conn.execute("SELECT * FROM customers WHERE customer_id = ?", (order["customer_id"],)).fetchone()
        if customer is None or not _identity_matches(customer, params.phone, params.email):
            return _NOT_VERIFIED
        return {"verified": True, "today": br_date(today), "orders": [_order_payload(conn, order, customer, today)]}

    # Without an order number: phone AND e-mail of the same customer record are required.
    candidates = conn.execute(
        "SELECT * FROM customers WHERE email_norm = ?", ((params.email or "").strip().lower(),)
    ).fetchall()
    customer = next((c for c in candidates if _phone_matches(c["phone_digits"], params.phone or "")), None)
    if customer is None:
        return _NOT_VERIFIED
    orders = conn.execute(
        "SELECT * FROM orders WHERE customer_id = ? ORDER BY order_date DESC", (customer["customer_id"],)
    ).fetchall()
    if not orders:
        return {"verified": True, "message": "Customer identified, but there are no orders on record.", "orders": []}
    return {
        "verified": True,
        "today": br_date(today),
        "orders": [_order_payload(conn, o, customer, today) for o in orders],
    }


LOOKUP_ORDERS = Tool(
    name="lookup_order",
    description=(
        "Looks up the customer's orders: status, items, total, payment method, tracking code, estimated delivery "
        "and, as of today, the status of the regret, exchange and warranty deadlines. "
        "Requires identity verification: order number + phone OR e-mail; or, without the number, phone AND e-mail. "
        "If the customer has not provided this data, ASK FOR IT before calling this tool. "
        "Use it for questions about the status, delivery, tracking, return, exchange or warranty of a purchase."
    ),
    input_model=OrderLookupInput,
    handler=lookup_orders,
)
