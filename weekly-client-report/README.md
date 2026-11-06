# Weekly client report

**What it does for an agency:** every agency sends clients a weekly update. This tool
turns one JSON file of store data into a finished report — computed metrics, a short
plain-English narrative, a clean HTML page and a text email body — in one command.
The hour a person spends assembling that email becomes a cron job.

**Internal demo build on sample data.** "Acme Outdoor Supply" is fictional; the data
file says so in its first line. In client work the input stage is replaced by a
connector to the real source (Shopify Admin API, GA4, a warehouse export).

## Run it

```bash
python3 report.py sample_data/acme_outdoor_week45.json --out out/          # Claude API → Ollama → fallback
python3 report.py sample_data/acme_outdoor_week45.json --out out/ --mock  # fully offline, deterministic
python3 -m unittest discover -s tests                                     # 23 tests
```

No dependencies beyond stock Python 3.11.

## How it's engineered (the part that matters)

- **Numbers and words are separated.** `metrics.py` is pure arithmetic — every figure
  in a report traces to the orders that produced it. The model (`narrate.py`) only
  writes the narrative and is explicitly told to use no number outside the metrics JSON.
- **Pluggable providers, never a hard failure:** Claude API when `ANTHROPIC_API_KEY`
  is set → local Ollama when a server answers (`OLLAMA_HOST` or localhost:11434) →
  deterministic template narrative. Every call gets timeouts and three retries with
  backoff on retryable statuses (429/5xx), and the footer names which source actually
  produced the words (`claude`, `ollama`, `mock`, or `fallback`).
- **Nothing sends by accident.** Default is dry-run; `--send` refuses to run unless
  all five SMTP environment variables are present.
- **Bad input fails loudly.** Missing fields, malformed items, duplicate order ids,
  negative totals and empty weeks are rejected with messages that say exactly what is
  wrong. An order's `total` is the amount actually charged (discounts already in it),
  so revenue never double-counts a discount.
- **Bounded reads.** The API response is capped by wall-clock deadline and size, so a
  trickling or oversized response can't hang the run — it falls back like any failure.
- **Idempotent output.** Same input, same filenames; in `--mock`, byte-identical files.

`sample_output/` holds a committed example produced with `--mock`;
`sample_output_ollama/` one produced live by a local llama3.2 via Ollama
(no API key, no vendor) — the footer names the source either way.
