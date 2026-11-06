# Lead-intake triage

**What it does for an agency:** every inbound lead costs someone ten minutes of
reading, judging and drafting a first reply. This tool turns an inbox of raw leads
into a scored, CRM-ready file plus a notification per lead with a draft reply a
human reviews — so the ten minutes becomes thirty seconds of review.

**Internal demo build on sample data.** All leads are fictional; the last sample is a
deliberately hostile one (spam + prompt-injection + spreadsheet-formula + terminal-
escape payloads) kept in to demonstrate the hardening below.

## Run it

```bash
python3 triage.py sample_data/leads_inbox.json --out out/          # Claude API → Ollama → fallback
python3 triage.py sample_data/leads_inbox.json --out out/ --mock  # fully offline, deterministic
python3 -m unittest discover -s tests                             # 20 tests
```

No dependencies beyond stock Python 3.11. Output is local files only (`crm_rows.csv`,
`notifications.txt`); pushing into a real CRM/Slack is the client-integration step and
deliberately doesn't exist in the demo.

## How it's engineered (the part that matters)

- **The model never decides the triage.** Scores and categories come from traceable
  rules (`rules.py`) computed *before* any model runs; the LLM only writes the summary
  and a draft reply, and its output is plain text for human review. The hostile sample
  literally asks for "category hot, score 100" — it gets spam, score 0, plus a warning.
- **Pluggable providers, never a hard failure:** Claude API when `ANTHROPIC_API_KEY`
  is set → local Ollama when a server answers → deterministic fallback. Every network
  read is bounded (60s wall-clock, 1 MB) with retries and backoff. Spam never reaches
  a model at all — no tokens spent on the exact text most likely to carry an injection.
- **Outputs are hardened where text gets interpreted:** CSV cells are
  formula-injection-safe (idempotent `'` prefix at the export boundary), and terminal/
  text output makes control and bidi characters visible instead of active.
- **One bad lead never takes down the batch** — it's skipped with a visible
  "NOT TRIAGED" record, and duplicates are refused.

`sample_output/` holds a committed example produced with `--mock`.
