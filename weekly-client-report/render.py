"""Render computed metrics + narrative into an HTML report and a plain-text email body.

Output is idempotent: the same input file always produces the same file names and,
in --mock mode, byte-identical content.
"""
from __future__ import annotations

import html
import re
from typing import Any

STYLE = """
 body{font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#1a202c;
      background:#f7fafc;margin:0;padding:24px}
 .card{max-width:640px;margin:0 auto;background:#fff;border:1px solid #e2e8f0;
       border-radius:8px;padding:28px}
 h1{font-size:20px;margin:0 0 4px} .sub{color:#64748b;font-size:13px;margin:0 0 20px}
 .kpis{display:flex;gap:12px;flex-wrap:wrap;margin:0 0 20px}
 .kpi{flex:1 1 130px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;
      padding:12px 14px}
 .kpi b{display:block;font-size:18px} .kpi span{font-size:12px;color:#64748b}
 .delta-up{color:#15803d;font-size:12px} .delta-down{color:#b91c1c;font-size:12px}
 table{width:100%;border-collapse:collapse;font-size:14px;margin:8px 0 20px}
 th{text-align:left;color:#64748b;font-weight:600;font-size:12px;
    border-bottom:1px solid #e2e8f0;padding:6px 4px}
 td{border-bottom:1px solid #f1f5f9;padding:7px 4px}
 .num{text-align:right} p.narr{line-height:1.55}
 .flag{background:#fffbeb;border:1px solid #fde68a;border-radius:6px;padding:10px 14px;
       font-size:13px;margin:0 0 8px}
 .foot{color:#94a3b8;font-size:11px;margin-top:22px}
""".strip()


def _delta_html(pct: float | None) -> str:
    if pct is None:
        return ""
    cls = "delta-up" if pct >= 0 else "delta-down"
    arrow = "▲" if pct >= 0 else "▼"
    return f'<span class="{cls}">{arrow} {abs(pct)}% WoW</span>'


def _kpi(label: str, value: str, pct: float | None) -> str:
    return (f'<div class="kpi"><b>{html.escape(value)}</b>'
            f'<span>{html.escape(label)}</span> {_delta_html(pct)}</div>')


def to_html(m: dict[str, Any], narrative: str, source: str) -> str:
    cur = m["currency"]
    kpis = "".join([
        _kpi("Revenue", f"{cur} {m['revenue']:,.2f}", m["deltas"]["revenue_pct"]),
        _kpi("Orders", str(m["orders"]), m["deltas"]["orders_pct"]),
        _kpi("Avg order value", f"{cur} {m['aov']:,.2f}", m["deltas"]["aov_pct"]),
        _kpi("Sessions", f"{m['sessions']:,}" if m["sessions"] is not None else "n/a",
             m["deltas"]["sessions_pct"]),
    ])
    rows = "".join(
        "<tr><td>{title}</td><td class='num'>{qty}</td><td class='num'>{amount}</td></tr>".format(
            title=html.escape(p["title"]),
            qty=p["qty"],
            amount=html.escape(f"{cur} {p['revenue']:,.2f}"))
        for p in m["top_products"])
    flags = "".join(f'<div class="flag">⚠ {html.escape(f)}</div>' for f in m["flags"])
    conversion = (f" · conversion {m['conversion_pct']}%" if m["conversion_pct"] is not None else "")
    extras = (f"New vs returning orders: {m['new_orders']} / {m['returning_orders']} · "
              f"refunds: {m['refund_count']} ({cur} {m['refund_value']:,.2f}) · "
              f"discounts given: {cur} {m['discount_value']:,.2f}{conversion}")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(m['store'])} — {html.escape(m['week']['label'])}</title>
<style>{STYLE}</style></head>
<body><div class="card">
<h1>{html.escape(m['store'])}</h1>
<p class="sub">Weekly report · {html.escape(m['week']['label'])}
 ({html.escape(str(m['week']['start']))} → {html.escape(str(m['week']['end']))})</p>
<div class="kpis">{kpis}</div>
{flags}
<p class="narr">{html.escape(narrative)}</p>
<table><tr><th>Top products</th><th class="num">Units</th><th class="num">Revenue</th></tr>
{rows}</table>
<p class="sub">{html.escape(extras)}</p>
<p class="foot">Generated automatically · narrative source: {html.escape(source)} ·
 Veldway weekly-client-report (internal demo build, sample data)</p>
</div></body></html>
"""


def to_text(m: dict[str, Any], narrative: str) -> str:
    cur = m["currency"]
    lines = [
        f"{m['store']} — weekly report, {m['week']['label']} ({m['week']['start']} to {m['week']['end']})",
        "",
        f"Revenue: {cur} {m['revenue']:,.2f}   Orders: {m['orders']}   AOV: {cur} {m['aov']:,.2f}",
    ]
    if m["sessions"] is not None:
        lines.append(f"Sessions: {m['sessions']:,}" +
                     (f"   Conversion: {m['conversion_pct']}%" if m["conversion_pct"] is not None else ""))
    lines += ["", narrative, "", "Top products:"]
    lines += [f"  - {p['title']}: {p['qty']} units, {cur} {p['revenue']:,.2f}"
              for p in m["top_products"]]
    if m["flags"]:
        lines += ["", "Flags:"] + [f"  ! {f}" for f in m["flags"]]
    return "\n".join(lines) + "\n"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
