"""Deterministic weekly metrics from raw order data.

Everything here is plain arithmetic on the input file: no network, no model.
The narrative layer (narrate.py) only ever sees the output of this module, so
every number in a report can be traced back to the orders that produced it.

Data semantics: an order's `total` is the amount actually charged, after any
discount; `discount` states the reduction already included in it (informational).
Revenue is therefore the sum of kept totals, with nothing subtracted twice.
`previous_week.refund_value` is informational and not used in any calculation.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

REQUIRED_TOP_LEVEL = ("store", "currency", "week", "orders")
REQUIRED_ORDER_FIELDS = ("id", "date", "total", "customer_type", "refunded", "items")


class InputError(ValueError):
    """The input file is not usable; the message says exactly why."""


def validate(data: dict[str, Any]) -> None:
    for field in REQUIRED_TOP_LEVEL:
        if field not in data:
            raise InputError(f"input is missing required field {field!r}")
    for field in ("label", "start", "end"):
        if field not in data["week"]:
            raise InputError(f"week is missing required field {field!r}")
    if not isinstance(data["orders"], list) or not data["orders"]:
        raise InputError("input has no orders; refusing to render an empty report")
    seen_ids: set[str] = set()
    for i, order in enumerate(data["orders"]):
        for field in REQUIRED_ORDER_FIELDS:
            if field not in order:
                raise InputError(f"order #{i} ({order.get('id', '?')}) is missing {field!r}")
        if order["id"] in seen_ids:
            raise InputError(f"duplicate order id {order['id']!r}")
        seen_ids.add(order["id"])
        if order["total"] < 0:
            raise InputError(f"order {order['id']!r} has a negative total")
        if not isinstance(order["items"], list) or not order["items"]:
            raise InputError(f"order {order['id']!r} has no items")
        for j, item in enumerate(order["items"]):
            for field in ("title", "qty", "price"):
                if field not in item:
                    raise InputError(f"order {order['id']!r} item #{j} is missing {field!r}")
            if not isinstance(item["qty"], (int, float)) or item["qty"] < 0:
                raise InputError(f"order {order['id']!r} item #{j} has a bad qty")
            if not isinstance(item["price"], (int, float)) or item["price"] < 0:
                raise InputError(f"order {order['id']!r} item #{j} has a bad price")


def _pct_change(current: float, previous: float) -> float | None:
    """Week-over-week change in percent; None when there is no usable baseline."""
    if previous <= 0:
        return None
    return round((current - previous) / previous * 100, 1)


def compute(data: dict[str, Any]) -> dict[str, Any]:
    """Aggregate one week of orders into the numbers the report shows."""
    validate(data)

    kept = [o for o in data["orders"] if not o["refunded"]]
    refunded = [o for o in data["orders"] if o["refunded"]]

    revenue = round(sum(o["total"] for o in kept), 2)
    orders_count = len(kept)
    aov = round(revenue / orders_count, 2) if orders_count else 0.0
    refund_value = round(sum(o["total"] for o in refunded), 2)
    discount_value = round(sum(o.get("discount", 0) for o in kept), 2)

    by_product: dict[str, dict[str, Any]] = defaultdict(lambda: {"qty": 0, "revenue": 0.0})
    for order in kept:
        for item in order["items"]:
            entry = by_product[item["title"]]
            entry["qty"] += item["qty"]
            entry["revenue"] = round(entry["revenue"] + item["qty"] * item["price"], 2)
    top_products = sorted(by_product.items(), key=lambda kv: kv[1]["revenue"], reverse=True)[:5]

    new_orders = sum(1 for o in kept if o["customer_type"] == "new")
    returning_orders = orders_count - new_orders

    prev = data.get("previous_week") or {}
    sessions = data.get("sessions")
    prev_orders = prev.get("orders", 0)
    prev_revenue = prev.get("revenue", 0)
    prev_aov = round(prev_revenue / prev_orders, 2) if prev_orders else 0

    deltas = {
        "revenue_pct": _pct_change(revenue, prev_revenue),
        "orders_pct": _pct_change(orders_count, prev_orders),
        "aov_pct": _pct_change(aov, prev_aov),
        "sessions_pct": _pct_change(sessions, prev.get("sessions", 0)) if sessions is not None else None,
    }

    conversion = round(orders_count / sessions * 100, 2) if sessions else None

    # Swings big enough that the narrative must mention them.
    flags = []
    for name, pct in (("revenue", deltas["revenue_pct"]), ("orders", deltas["orders_pct"]),
                      ("average order value", deltas["aov_pct"]), ("sessions", deltas["sessions_pct"])):
        if pct is not None and abs(pct) >= 15:
            flags.append(f"{name} {'up' if pct > 0 else 'down'} {abs(pct)}% week-over-week")
    if refund_value and revenue and refund_value / revenue >= 0.05:
        flags.append(f"refunds reached {round(refund_value / revenue * 100, 1)}% of revenue")

    return {
        "store": data["store"],
        "currency": data["currency"],
        "week": data["week"],
        "revenue": revenue,
        "orders": orders_count,
        "aov": aov,
        "sessions": sessions,
        "conversion_pct": conversion,
        "refund_count": len(refunded),
        "refund_value": refund_value,
        "discount_value": discount_value,
        "new_orders": new_orders,
        "returning_orders": returning_orders,
        "top_products": [{"title": t, **v} for t, v in top_products],
        "deltas": deltas,
        "flags": flags,
    }
