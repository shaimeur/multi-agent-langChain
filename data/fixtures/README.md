# Recorded fixtures

Every external call FORGE makes — LLM completions included — is recorded here as
readable JSON and replayed under `CACHE_MODE=replay`.

**These files are committed on purpose.** A fresh clone with no API keys must be
able to reproduce the graded demo. That is what makes the demo survive a spent
free-tier quota, a provider outage, or bad conference Wi-Fi — strictly more
failure modes than the Ollama offline profile covers.

Layout mirrors the namespace: `llm.planner` → `llm/planner/<key>.json`.
The key is a digest of the request payload, so it is stable across dict ordering
and changes when the model or temperature changes.

Secrets are stripped on write (`src/forge/cache/fixtures.py`) and never take part
in a cache key. Do not hand-edit these files; re-record with `CACHE_MODE=refresh`.
