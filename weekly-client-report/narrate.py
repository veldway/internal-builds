"""Turn computed metrics into a short plain-English narrative.

Provider chain, most capable first, never a hard failure:
  1. Claude API      — when ANTHROPIC_API_KEY is set,
  2. Ollama          — when a local server answers (OLLAMA_HOST or localhost:11434),
  3. fallback        — a deterministic template narrative built only from the metrics.
Plus --mock: a canned narrative for deterministic test runs.

The pipeline NEVER fails because a model is unreachable: any provider problem is
logged and the next path is used, so the client still gets a report.
"""
from __future__ import annotations

import http.client
import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger("weekly-report")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-sonnet-5-5"
OLLAMA_DEFAULT = "http://localhost:11434"
TIMEOUT_S = 30          # socket-level timeout per read/connect
TOTAL_DEADLINE_S = 60   # wall-clock bound for the whole request, body included
MAX_BODY_BYTES = 1_000_000
RETRIES = 3
RETRYABLE = {429, 500, 502, 503, 504, 529}

PROMPT = """You are writing the summary paragraph of a weekly e-commerce report an
agency sends its client. Using ONLY the JSON metrics below, write 3-5 sentences in
plain, factual English: what revenue and orders did week-over-week, the strongest
product, and anything in "flags". No advice unless a flag clearly implies one next
step. Never invent a number that is not in the JSON. Reply with the summary
paragraph only — no preamble, no headings.

Metrics:
{metrics}"""


def _bounded_read(resp) -> bytes:
    # A socket timeout resets on every byte, so a trickling server could hold a
    # plain read() open indefinitely. Bound the whole body by wall clock and size.
    deadline = time.monotonic() + TOTAL_DEADLINE_S
    chunks: list[bytes] = []
    size = 0
    while True:
        if time.monotonic() > deadline:
            raise TimeoutError(f"response body exceeded the {TOTAL_DEADLINE_S}s deadline")
        chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise ValueError(f"response body exceeded {MAX_BODY_BYTES} bytes")
        chunks.append(chunk)


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers={"content-type": "application/json", **headers},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(_bounded_read(resp).decode("utf-8"))


def _with_retries(call) -> str:
    last_error: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            text = call()
            if not text.strip():
                raise ValueError("provider returned an empty narrative")
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
    raise RuntimeError(f"provider unavailable after {RETRIES} attempts: {last_error}")


def _claude(prompt: str, api_key: str) -> str:
    body = _post_json(ANTHROPIC_URL,
                      {"model": MODEL, "max_tokens": 400,
                       "messages": [{"role": "user", "content": prompt}]},
                      {"x-api-key": api_key, "anthropic-version": "2023-06-01"})
    return "".join(block.get("text", "") for block in body.get("content", []))


def _ollama(prompt: str, host: str) -> str:
    body = _post_json(f"{host.rstrip('/')}/api/generate",
                      {"model": os.environ.get("OLLAMA_MODEL", "llama3.2"),
                       "prompt": prompt, "stream": False}, {})
    return str(body.get("response", ""))


def ollama_host() -> str | None:
    """The local Ollama server's base URL, or None when nothing answers.

    The probe must never crash the report: a malformed OLLAMA_HOST (whitespace,
    control characters, a bad port) reads as "no server", not as an exception.
    """
    host = os.environ.get("OLLAMA_HOST", "").strip() or OLLAMA_DEFAULT
    if not host.startswith("http"):
        host = "http://" + host
    try:
        with urllib.request.urlopen(f"{host.rstrip('/')}/api/tags", timeout=1) as resp:
            resp.read(1)
        return host
    except (OSError, ValueError, http.client.HTTPException):
        return None


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
    """Return (text, source) where source is one of: claude, ollama, mock, fallback."""
    if mock:
        return MOCK_NOTE + fallback_narrative(metrics), "mock"
    prompt = PROMPT.format(metrics=json.dumps(metrics, indent=2))
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        try:
            return _with_retries(lambda: _claude(prompt, api_key)).strip(), "claude"
        except RuntimeError as err:
            log.error("%s — trying the next provider", err)
    else:
        log.warning("ANTHROPIC_API_KEY not set; trying Ollama, then the deterministic fallback")
    host = ollama_host()
    if host:
        try:
            return _with_retries(lambda: _ollama(prompt, host)).strip(), "ollama"
        except RuntimeError as err:
            log.error("%s — using the deterministic fallback", err)
    return fallback_narrative(metrics), "fallback"
