"""Turn computed metrics into a short plain-English narrative.

Three paths, in order of preference:
  1. Claude API when ANTHROPIC_API_KEY is set (and --mock not given),
  2. --mock: a canned narrative for deterministic test runs,
  3. fallback: a template narrative built only from the metrics.

The pipeline NEVER fails because the model is unreachable: any API problem is
logged and the fallback narrative is used, so the client still gets a report.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger("weekly-report")

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-5-5"
TIMEOUT_S = 30
RETRIES = 3
RETRYABLE = {429, 500, 502, 503, 504, 529}

PROMPT = """You are writing the summary paragraph of a weekly e-commerce report an
agency sends its client. Using ONLY the JSON metrics below, write 3-5 sentences in
plain, factual English: what revenue and orders did week-over-week, the strongest
product, and anything in "flags". No advice unless a flag clearly implies one next
step. Never invent a number that is not in the JSON.

Metrics:
{metrics}"""


def _request(payload: bytes, api_key: str) -> dict[str, Any]:
    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _call_claude(metrics: dict[str, Any], api_key: str) -> str:
    payload = json.dumps({
        "model": MODEL,
        "max_tokens": 400,
        "messages": [{
            "role": "user",
            "content": PROMPT.format(metrics=json.dumps(metrics, indent=2)),
        }],
    }).encode("utf-8")

    last_error: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            body = _request(payload, api_key)
            text = "".join(block.get("text", "") for block in body.get("content", [])).strip()
            if not text:
                raise ValueError("API returned an empty narrative")
            return text
        except urllib.error.HTTPError as err:
            last_error = err
            if err.code not in RETRYABLE:
                break  # 4xx other than 429: retrying would not help
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as err:
            last_error = err
        if attempt < RETRIES:
            delay = 2 ** attempt
            log.warning("narrative attempt %d/%d failed (%s); retrying in %ds",
                        attempt, RETRIES, last_error, delay)
            time.sleep(delay)
    raise RuntimeError(f"Claude API unavailable after {RETRIES} attempts: {last_error}")


def _fmt_pct(pct: float | None) -> str:
    if pct is None:
        return "with no prior-week baseline"
    direction = "up" if pct > 0 else "down" if pct < 0 else "flat"
    return f"{direction} {abs(pct)}% week-over-week" if pct else "flat week-over-week"


def fallback_narrative(m: dict[str, Any]) -> str:
    """Deterministic narrative from the metrics alone. Always available."""
    cur = m["currency"]
    lines = [
        f"{m['store']} took {m['orders']} orders for {cur} {m['revenue']:,.2f} "
        f"in {m['week']['label']} ({_fmt_pct(m['deltas']['revenue_pct'])}).",
        f"Average order value was {cur} {m['aov']:,.2f} "
        f"({_fmt_pct(m['deltas']['aov_pct'])}).",
    ]
    if m["top_products"]:
        top = m["top_products"][0]
        lines.append(f"The strongest product was {top['title']} "
                     f"({top['qty']} units, {cur} {top['revenue']:,.2f}).")
    if m["flags"]:
        lines.append("Worth noting: " + "; ".join(m["flags"]) + ".")
    return " ".join(lines)


MOCK_NOTE = ("[mock narrative] This run used --mock, so the summary below comes from "
             "the deterministic template rather than the model. ")


def narrative(metrics: dict[str, Any], mock: bool = False) -> tuple[str, str]:
    """Return (text, source) where source is one of: claude, mock, fallback."""
    if mock:
        return MOCK_NOTE + fallback_narrative(metrics), "mock"
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        log.warning("ANTHROPIC_API_KEY not set; using the deterministic fallback narrative")
        return fallback_narrative(metrics), "fallback"
    try:
        return _call_claude(metrics, api_key), "claude"
    except RuntimeError as err:
        log.error("%s — falling back to the deterministic narrative", err)
        return fallback_narrative(metrics), "fallback"
