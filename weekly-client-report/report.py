#!/usr/bin/env python3
"""Weekly client report: one JSON of store data in, an HTML + text report out.

    python3 report.py sample_data/acme_outdoor_week45.json --out out/
    python3 report.py data.json --out out/ --mock          # no API, deterministic
    python3 report.py data.json --out out/ --send          # also email it (SMTP_* env)

Default is dry-run: files are written, nothing is sent. --send requires SMTP_HOST,
SMTP_PORT, SMTP_USER, SMTP_PASSWORD and REPORT_TO in the environment and refuses to
run without all five, so a report can never leave the machine by accident.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import smtplib
import sys
from email.message import EmailMessage

import metrics as metrics_mod
import narrate
import render

log = logging.getLogger("weekly-report")

SMTP_VARS = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "REPORT_TO")


def send_email(subject: str, text: str, html_body: str) -> None:
    missing = [v for v in SMTP_VARS if not os.environ.get(v)]
    if missing:
        raise SystemExit(f"--send refused: missing environment variables {', '.join(missing)}")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["REPORT_TO"]
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ["SMTP_PORT"]), timeout=30) as smtp:
        smtp.starttls()
        smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        smtp.send_message(msg)
    log.info("report emailed to %s", os.environ["REPORT_TO"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", help="store data JSON for one week")
    parser.add_argument("--out", default="out", help="output directory (default: out)")
    parser.add_argument("--mock", action="store_true",
                        help="deterministic narrative; never calls the API")
    parser.add_argument("--send", action="store_true",
                        help="email the report (requires SMTP_* and REPORT_TO env)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        data = json.loads(pathlib.Path(args.input).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"input file not found: {args.input}")
    except json.JSONDecodeError as err:
        raise SystemExit(f"input is not valid JSON: {err}")

    try:
        computed = metrics_mod.compute(data)
    except metrics_mod.InputError as err:
        raise SystemExit(f"bad input: {err}")

    text_narr, source = narrate.narrative(computed, mock=args.mock)
    html_report = render.to_html(computed, text_narr, source)
    text_report = render.to_text(computed, text_narr)

    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"report_{render.slug(computed['store'])}_{render.slug(computed['week']['label'])}"
    html_path = out_dir / f"{base}.html"
    text_path = out_dir / f"{base}.txt"
    html_path.write_text(html_report, encoding="utf-8")
    text_path.write_text(text_report, encoding="utf-8")
    log.info("wrote %s and %s (narrative source: %s)", html_path, text_path, source)

    if args.send:
        send_email(f"{computed['store']} — {computed['week']['label']} report",
                   text_report, html_report)
    else:
        log.info("dry-run: nothing sent (use --send with SMTP_* env to email)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
