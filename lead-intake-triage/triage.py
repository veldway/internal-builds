#!/usr/bin/env python3
"""Lead-intake triage: an inbox of raw leads in, a scored CRM file + notifications out.

    python3 triage.py sample_data/leads_inbox.json --out out/
    python3 triage.py sample_data/leads_inbox.json --out out/ --mock   # offline, deterministic

Dry-run by design: everything is written to local files (crm_rows.csv,
notifications.txt). Pushing rows into a real CRM or Slack is the client-integration
step and deliberately doesn't exist in the demo.
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

import outputs
import rules
import summarize

log = logging.getLogger("lead-triage")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("input", help="JSON file with a 'leads' list")
    parser.add_argument("--out", default="out", help="output directory (default: out)")
    parser.add_argument("--mock", action="store_true",
                        help="deterministic prose; never calls any model")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        data = json.loads(pathlib.Path(args.input).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"input file not found: {args.input}")
    except json.JSONDecodeError as err:
        raise SystemExit(f"input is not valid JSON: {err}")
    leads = data.get("leads") if isinstance(data, dict) else None
    if not isinstance(leads, list) or not leads:
        raise SystemExit("input has no 'leads' list")

    out_dir = pathlib.Path(args.out)
    crm_rows: list[dict[str, str]] = []
    notes: list[str] = []
    failures = 0
    seen_ids: set[str] = set()
    for lead in leads:
        # One bad lead must not take down the batch (the inbox is untrusted input).
        try:
            verdict = rules.triage(lead)
            if verdict["id"] in seen_ids:
                raise rules.InputError(f"duplicate lead id {verdict['id']!r}")
            seen_ids.add(verdict["id"])
            summary, reply, source = summarize.prose(lead, verdict, mock=args.mock)
            crm_rows.append(outputs.crm_row(lead, verdict, summary, reply, source))
            notes.append(outputs.notification(lead, verdict, summary, reply, source))
        except rules.InputError as err:
            failures += 1
            notes.append(f"[NOT TRIAGED] {outputs.clean_text(err)}")
            log.error("skipped a lead: %s", outputs.clean_text(err))

    outputs.write_crm_csv(crm_rows, out_dir / "crm_rows.csv")
    (out_dir / "notifications.txt").write_text("\n\n".join(notes) + "\n", encoding="utf-8")
    log.info("triaged %d lead(s), skipped %d; wrote %s and %s",
             len(crm_rows), failures, out_dir / "crm_rows.csv", out_dir / "notifications.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
