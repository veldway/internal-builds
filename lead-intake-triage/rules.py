"""Deterministic lead triage: every point of the score is traceable to a rule.

The model (summarize.py) never decides the score or the category — it only writes
prose afterwards. That keeps triage auditable: a client can ask "why was this lead
hot?" and get the exact rules that fired.
"""
from __future__ import annotations

import re
from typing import Any

REQUIRED_FIELDS = ("id", "received_at", "source", "name", "email", "company", "message")

URGENCY = re.compile(r"\b(urgent|asap|this week|today|deadline|by friday|right away|"
                     r"next two weeks|immediately)\b", re.I)
FIT = re.compile(r"\b(shopify|klaviyo|liquid|theme|checkout|inventory|integration|"
                 r"automation|erp|sync|flows?|retention|crm)\b", re.I)
BUDGET = re.compile(r"(?:\$|usd\s?|eur\s?|£|€)\s?(\d[\d,]*(?:\.\d+)?)\s?(k\b)?", re.I)
REFERRAL_SOURCES = {"referral", "partner", "introduction"}
SPAM_PHRASES = re.compile(r"\b(guaranteed|#1 google|backlinks?|seo blast|crypto payment|"
                          r"100% free|limited offer|act now)\b", re.I)
LINK = re.compile(r"https?://", re.I)
INJECTION = re.compile(r"\b(ignore (all )?(previous|prior|above) instructions?|"
                       r"disregard (the )?(previous|prior|above)|you are now|"
                       r"system prompt)\b", re.I)


class InputError(ValueError):
    """The lead is not usable; the message says exactly why."""


def validate(lead: dict[str, Any]) -> None:
    for field in REQUIRED_FIELDS:
        if field not in lead:
            raise InputError(f"lead {lead.get('id', '?')!r} is missing {field!r}")
    if not str(lead["message"]).strip():
        raise InputError(f"lead {lead['id']!r} has an empty message")


def budget_amount(message: str) -> float | None:
    """The money figure the lead names as their budget.

    A message can carry several figures ("we do $80k/month ... budget is $3,000"),
    and the largest is usually revenue, not budget. Prefer figures whose nearby
    text says budget/spend; only when none does, fall back to the largest figure.
    """
    near_budget: list[float] = []
    all_figures: list[float] = []
    for match in BUDGET.finditer(message):
        value = float(match.group(1).replace(",", ""))
        if match.group(2):  # "3k" style
            value *= 1000
        all_figures.append(value)
        context = message[max(0, match.start() - 40):match.start()].lower()
        if "budget" in context or "spend" in context:
            near_budget.append(value)
    if near_budget:
        return max(near_budget)
    return max(all_figures) if all_figures else None


def triage(lead: dict[str, Any]) -> dict[str, Any]:
    """Score one validated lead. Returns score, category and the reasons that fired."""
    validate(lead)
    message = str(lead["message"])
    reasons: list[str] = []
    score = 0

    # Spam and injection signals are judged first and disqualify.
    spam_points = 0
    if SPAM_PHRASES.search(message):
        spam_points += 2
        reasons.append("spam phrases in message")
    if len(LINK.findall(message)) >= 3:
        spam_points += 1
        reasons.append("3+ links in message")
    if not str(lead["name"]).strip():
        spam_points += 1
        reasons.append("no sender name")
    injection = bool(INJECTION.search(message))
    if injection:
        spam_points += 2
        reasons.append("prompt-injection phrasing in message")

    if spam_points >= 2:
        return {"id": lead["id"], "score": 0, "category": "spam",
                "reasons": reasons, "budget": None, "injection_suspected": injection}

    if FIT.search(message):
        hits = sorted({m.group(0).lower() for m in FIT.finditer(message)})
        score += min(30, 10 * len(hits))
        reasons.append("service fit: " + ", ".join(hits))
    if URGENCY.search(message):
        score += 25
        reasons.append("urgency language")
    budget = budget_amount(message)
    if budget is not None:
        score += 25 if budget >= 1000 else 10
        reasons.append(f"budget named (~{budget:,.0f})")
    if str(lead["source"]).lower() in REFERRAL_SOURCES:
        score += 20
        reasons.append("came via referral")
    if len(message) >= 120:
        score += 5
        reasons.append("substantive message")

    category = "hot" if score >= 70 else "qualified" if score >= 40 else "nurture"
    return {"id": lead["id"], "score": score, "category": category,
            "reasons": reasons, "budget": budget, "injection_suspected": injection}
