# Veldway internal builds

Working automation builds by [Veldway](https://veldway.com) — the internal builds
behind our AI Automation service. Each directory is a small, genuinely runnable tool
of the kind we build for clients; the full write-ups live on our site, under Work.

**Honesty labeling:** these are internal demo builds on sample data, created to show
how we engineer automations — input validation, retries, graceful fallbacks, tests,
dry-run-by-default sending. They are not client projects and are never presented as
client results.

| Demo | What it automates |
|---|---|
| [weekly-client-report](weekly-client-report/) | A store's weekly numbers → metrics → plain-English narrative (Claude → Ollama → deterministic fallback) → HTML + text report, optionally emailed |
| [lead-intake-triage](lead-intake-triage/) | An inbox of raw leads → rule-scored triage (hot/qualified/nurture/spam) → CRM-ready CSV + per-lead notification with a human-reviewed draft reply (Claude → Ollama → deterministic fallback; injection-hardened) |
