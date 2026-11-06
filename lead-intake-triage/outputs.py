"""Render triage results: a CRM-ready CSV and a plain-text notification.

Hardening lives here because this is where untrusted lead text reaches things that
interpret it:
- Spreadsheets treat leading = + - @ as formulas, so every untrusted CSV cell goes
  through cell_safe() (idempotent apostrophe prefix; applied at the export boundary,
  so rows from any writer are covered).
- Terminals interpret control characters, so everything printed or written as text
  goes through clean_text(), which makes C0/C1 controls and bidi/zero-width marks
  visible as escapes instead of executing them.
"""
from __future__ import annotations

import csv
import pathlib
import re
from typing import Any

CRM_COLUMNS = ("id", "received_at", "category", "score", "name", "email", "company",
               "website", "source", "budget", "reasons", "summary", "suggested_reply",
               "prose_source")

_CONTROLS = re.compile(
    "[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f"
    "​-‏‪-‮⁦-⁩]")


def clean_text(value: Any) -> str:
    """Make control and bidi characters visible instead of active."""
    return _CONTROLS.sub(lambda m: "\\u%04x" % ord(m.group(0)), str(value))


def cell_safe(value: Any) -> str:
    """Neutralise spreadsheet formula injection; idempotent."""
    text = clean_text(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r")) :
        return "'" + text
    return text


def crm_row(lead: dict[str, Any], verdict: dict[str, Any],
            summary: str, reply: str, source: str) -> dict[str, str]:
    raw = {
        "id": lead["id"], "received_at": lead["received_at"],
        "category": verdict["category"], "score": verdict["score"],
        "name": lead.get("name", ""), "email": lead.get("email", ""),
        "company": lead.get("company", ""), "website": lead.get("website", ""),
        "source": lead.get("source", ""),
        "budget": "" if verdict["budget"] is None else f"{verdict['budget']:,.0f}",
        "reasons": "; ".join(verdict["reasons"]),
        "summary": summary, "suggested_reply": reply, "prose_source": source,
    }
    return {key: cell_safe(raw[key]) for key in CRM_COLUMNS}


def write_crm_csv(rows: list[dict[str, str]], path: pathlib.Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CRM_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def notification(lead: dict[str, Any], verdict: dict[str, Any],
                 summary: str, reply: str, source: str) -> str:
    lines = [
        f"[{verdict['category'].upper()} · {verdict['score']}] "
        f"{clean_text(lead.get('name') or '(no name)')} — "
        f"{clean_text(lead.get('company', ''))} · {clean_text(lead.get('email', ''))}",
        f"  why: {clean_text('; '.join(verdict['reasons']) or 'no signals')}",
        f"  summary ({source}): {clean_text(summary)}",
    ]
    if verdict["injection_suspected"]:
        lines.append("  ⚠ message contained prompt-injection phrasing — treat with care")
    if verdict["category"] != "spam":
        lines.append(f"  draft reply (HUMAN REVIEWS BEFORE SENDING): {clean_text(reply)}")
    return "\n".join(lines)
