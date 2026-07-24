<!-- The mutable progress ledger. Rewritten in full at every /checkpoint, never
     appended to. Keep it under 80 lines. Nothing goes under "Done (verified)"
     unless its DoD command actually ran and passed this or a named session.
     GOALS.md is the durable plan; this file is where you are in it. -->

Last updated : 2026-07-24
Roadmap day  : D11 DoD met · Sprint 3 complete · D12 (FastAPI + SSE, CLI) is next
Branch       : main
Last commit  : 9d876d7 [D11] Red team suite — 32/32 attacks mitigated, pass rate computed not claimed

## Done (verified)
- [x] D1 foundations · D2 AST ingestion (617 chunks) · D3 hybrid retrieval · Grounded-RAG (D5-pre)
- [x] **D4 RAG eval + freeze** — MiniLM FROZEN (R@10 0.905 vs BGE 0.857); reranker OFF
- [x] **D5 LangGraph + memory** — AsyncSqliteSaver, restart proven offline
- [x] **D6 Planner + Editor** — worktree, `git apply --check`, grounding enforced in code
- [x] **D7 Sandbox** — container per run + documented fallback; `docs/limitations.md`; 15 flags
      verified via `docker inspect`
- [x] **D8 Tester + repair loop** — regression-test-first; `evals/swe_mini/` (4 bugs + hidden
      tests); broken function repaired in **2 iterations** (scripted model)
- [x] **D9 Reviewer + HITL** — 5 fixed points, **3 of 5 never reach a model**; `interrupt()` at both
      §5.5 gates; strict checkpoint serde (`core/checkpoint.py`)
- [x] **D10 Guardrails** — `guardrails/` (events log, policy, injection, both sentinels);
      `GET /v1/guardrails/events` + `/summary`; **wired live** (retriever + `/v1/ask`)
- [x] **D11 Red team — DoD MET** (2026-07-24). `evals/security/` — 32 cases over all twelve §13.4
      classes; pass rate computed from pytest outcomes, not hand-kept. `.github/workflows/ci.yml`.
      DoD — `uv run pytest evals/security -q` → **32/32 mitigated, 0 breached, exit 0**, with
      SEC-01/SEC-02 reported as deliberate deviations (limitations.md §6).
      Full — `CACHE_MODE=replay uv run pytest` → **322 passed, 5 skipped, exit 0**;
      `ruff check src tests evals` → exit 0. **C5 CLOSED — first gate**: suite green + a real
      `POST /v1/ask` (HTTP 400) leaves an event `curl .../events | jq length` reports
- [x] Continuity system — .claude/ hooks, STATE.md, /checkpoint, CLAUDE.md

## In progress
- D12 FastAPI + SSE + CLI. NEXT: the §11 routes the graph now needs — `POST /v1/sessions`,
  `POST /v1/sessions/{id}/messages`, **`POST /v1/sessions/{id}/approve`** (the resume endpoint for
  D9's two `interrupt()` gates — `Command(resume=...)` already works, it needs an HTTP surface),
  `GET /v1/sessions/{id}/events` (SSE via sse-starlette), plus `forge tools` listing 10.
  **C8 needs** `curl -s localhost:8000/openapi.json | jq '.paths|keys'` showing all §11 routes and
  `scripts/sse_smoke.sh` streaming. Read GOALS.md D12 first.
- **Carried**: injection tier 2 (O5, needs a human call) · `notebooks/02_agent_traces.ipynb`
  (L3 part 2, C2 depends on it, wants a real-model trace).

## Blocked / open decisions
- **B2 — no cloud key** (`.env` has none; a hook blocks reading it — check booleans via
  `Settings.google_api_key`). Only `mistral:latest`. D6–D9 are **mechanism-proven with scripted
  models**; no real model has driven planner/editor/tester/reviewer, and swe_mini has never run for
  real. D10–D11 are deterministic and were NOT blocked. **Must be resolved before D14's demo.**
- **O5 — injection tier 2** not built (only tier 1). Argued in limitations.md §7: per-chunk
  transformer on every pack, on a CPU box where D4 measured a cross-encoder at 14 ms → 2589 ms p95;
  judge tier needs B2's key. **Accepting it properly needs a `descope-v1.md` entry — that file is
  frozen, so this needs the human.** Ties to the SEC-01/02 deviation (limitations.md §6).
- **O4 — reviewer/editor model family.** §4/A5 wants different families; FORGE splits by role
  (CODER vs REASONER), one model under one provider. `shares_family_with_editor()` reports it.
- B3 — `qwen2.5-coder:7b` unpulled. O1 React vs Streamlit (descope §2) — ASK THE HUMAN. O2 C6
  "Tools AND MCP" wording. D1 compose DoD not re-run — ? unverified. **O3** — `make lint` red since
  before D7 (`rag/embed.py`, `tests/test_graph.py` fail `ruff format --check`); one `make fmt` fixes it.
- **Gates: 1 of C1–C10 closed (C5) at D11 of D15.** C1 is still nearly free — 8 agents exist, it
  only wants `tests/test_agents.py -k distinct`. C8 is D12's job.

## Do not redo
- **The security suite is built** (D11). Its pass rate is *derived* from pytest outcomes via the
  `attack` marker + `pytest_collection_modifyitems` map (a `TestReport` carries no markers). Do not
  replace it with a hand-kept tally. `evals/results/` is gitignored; CI uploads the JSON.
- **Guardrails are built AND wired** (D10). The log **never raises** — both paths catch `Exception`,
  not `sqlite3.Error`, because `_connect` fails on the filesystem first. `check_answer(answer, None)`
  = "verified upstream" for the direct `/v1/ask` path; an empty pack would drop every citation.
  Scanned chunks are **copied**, never mutated.
- **Review + HITL** (D9): the patch gate sits *between* `editor` (never writes) and `apply`. Planner
  exits are parameters so no graph can bypass the plan gate. Strict checkpoint serde — use
  `sqlite_checkpointer()` / `MemorySaver(serde=forge_serde())`, never a bare saver.
- **Sandbox** (D7): `RLIMIT_DATA` not `RLIMIT_AS`. Output head-truncated → **exit code is the
  authority**. compose does **not** mount docker.sock. **MiniLM FROZEN**, reranker OFF (D4).

## Notes for the next session
- Commits OMIT the Claude co-author trailer (author Shaimeur). `pythonpath = ["."]` and isort
  first-party = `forge` + `evals`. `testpaths = ["tests"]`, so the security suite runs only when
  named — CI runs it as its own step. The 5 skipped tests are container-only under the fallback param.
- `sandbox/` + `guardrails/` are security-sensitive (CLAUDE.md): flag any flag/cap/allowlist change.
- `make sandbox-image` builds `forge-sandbox:latest`. Embedded Qdrant: ONE client per path per process.
