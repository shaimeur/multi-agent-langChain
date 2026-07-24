<!-- The mutable progress ledger. Rewritten in full at every /checkpoint, never
     appended to. Keep it under 80 lines. Nothing goes under "Done (verified)"
     unless its DoD command actually ran and passed this or a named session.
     GOALS.md is the durable plan; this file is where you are in it. -->

Last updated : 2026-07-24
Roadmap day  : D10 DoD met (tier 2 carried) · D11 (Red team + security suite) is next
Branch       : main
Last commit  : f7c34e6 [D10] Guardrails — three deterministic layers and a queryable event log

## Done (verified)
- [x] D1 foundations · D2 AST ingestion (617 chunks) · D3 hybrid retrieval · Grounded-RAG (D5-pre)
- [x] **D4 RAG eval + config freeze** — MiniLM FROZEN (R@10 0.905 vs BGE 0.857); reranker OFF
- [x] **D5 LangGraph + memory** — AsyncSqliteSaver, restart proven offline
- [x] **D6 Planner + Editor** — worktree, `git apply --check`, grounding enforced in code
- [x] **D7 Sandbox** — container per run + documented fallback; `docs/limitations.md`; 15 flags
      verified via `docker inspect`
- [x] **D8 Tester + repair loop** — regression-test-first, `evals/swe_mini/` (4 bugs + hidden
      tests). Broken function repaired in **2 iterations**
- [x] **D9 Reviewer + HITL** — 5 fixed points, **3 of 5 never reach a model**; `interrupt()` at both
      §5.5 gates; strict checkpoint serde (`core/checkpoint.py`)
- [x] **D10 Guardrails — DoD MET** (2026-07-24). `guardrails/` (was an empty stub): `events.py`
      (the log — a never-cut item), `policy.py` (realpath-first path whitelist, 5-command whitelist),
      `injection.py` (spotlighting, stripping, privilege invariance), `sentinel_in.py`,
      `sentinel_out.py`; `GET /v1/guardrails/events` + `/summary`. **Wired into the live path**:
      `scan_chunks` in the retriever node, both sentinels on `POST /v1/ask`.
      DoD — live server, `curl '.../v1/guardrails/events?session_id=demo' | jq length` → **5**,
      across all three layers. Full — `CACHE_MODE=replay uv run pytest` → **322 passed, 5 skipped,
      exit 0**; `ruff check src tests evals` → exit 0
- [x] Continuity system — .claude/ hooks, STATE.md, /checkpoint, CLAUDE.md

## In progress
- D11 Red team + security suite. NEXT: `evals/security/` — the adversarial corpus D10 built the
  defences for. Direct injection, indirect (poisoned repo comments), path escape, command injection,
  secret exfiltration, resource exhaustion. **This is the other half of C5** (`uv run pytest
  evals/security -q` green). Then the §17 threat-model writeup. Read GOALS.md D11 first.
- **Carried from D10**: injection **tier 2** (DeBERTa classifier + LLM judge on the ambiguous
  middle). `classify()` is the seam. See open decisions — needs a human call.
- **Carried from D8**: `notebooks/02_agent_traces.ipynb` (L3 part 2). C2 depends on it.

## Blocked / open decisions
- **B2 — no cloud key** (`.env` has none; a hook blocks reading it — check booleans via
  `Settings.google_api_key`). Only `mistral:latest`. Everything D6–D9 is **mechanism-proven with
  scripted models**; no real model has driven planner/editor/tester/reviewer. D10/D11 are
  deterministic and NOT blocked. A real-model run must happen before D14's demo.
- **NEW O5 — injection tier 2.** §8.1 wants heuristics → DeBERTa-class classifier → LLM judge.
  Only tier 1 is built. Argument for deferring: per-chunk transformer on every pack, on a CPU-only
  box where D4 measured a cross-encoder at 14 ms → 2589 ms p95; the judge tier needs B2's key.
  **Decide: accept as a descope (needs a descope-v1 entry, which is frozen — raise it), or build it.**
- **O4 — reviewer/editor model family.** §4/A5 wants different families; FORGE splits by role
  (CODER vs REASONER), which under one provider is one model. `shares_family_with_editor()` reports it.
- B3 — `qwen2.5-coder:7b` unpulled. O1 React vs Streamlit (descope §2) — ASK THE HUMAN.
  O2 C6 "Tools AND MCP" wording. D1 compose DoD not re-run — ? unverified.
- **O3 — `make lint` red since before D7**: `src/forge/rag/embed.py`, `tests/test_graph.py` fail
  `ruff format --check` (pre-existing). One `make fmt` fixes both.
- **Acceptance gates: 0 of C1–C10 ticked at D10 of D15.** C1 is nearly free (8 agents exist; it only
  wants `tests/test_agents.py -k distinct`). C5 is half-closed by D10 — D11 closes the rest.

## Do not redo
- **Guardrails are built AND wired** (D10). The log's contract is that it *never raises* — the read
  and write paths catch `Exception`, not `sqlite3.Error`, because `_connect` fails on the filesystem
  first; narrowing it would make a guardrail fail open. `check_answer(answer, None)` = "citations
  already verified upstream", used by the direct `/v1/ask` path; passing an empty pack instead would
  drop every citation. Chunks are **copied** when scanned, never mutated.
- **Review + HITL** (D9): the patch gate sits *between* `editor` (builds, never writes) and `apply`.
  Planner exits are parameters so no graph can bypass the plan gate.
- **Strict checkpoint serde** (`core/checkpoint.py`): allowlist = `forge.models` + `Budget`. Use
  `sqlite_checkpointer()` / `MemorySaver(serde=forge_serde())`, never a bare saver.
- **Sandbox** (D7): `RLIMIT_DATA` not `RLIMIT_AS` (ruff SIGABRTed 8/10 under AS). Output
  head-truncated at 64 KB → the **exit code is the authority**. compose does **not** mount docker.sock.
- **MiniLM FROZEN**, reranker OFF (D4). sqlparse @0d24023.

## Notes for the next session
- Commits OMIT the Claude co-author trailer (author Shaimeur). `pyproject.toml` sets
  `pythonpath = ["."]`; isort knows `forge` + `evals` as first-party.
- `src/forge/sandbox/` and `guardrails/` are security-sensitive (CLAUDE.md): flag any flag/cap/
  path-allowlist change first. The 5 skipped tests are container-only assertions under the fallback
  param — correct, not a coverage gap.
- `make sandbox-image` builds `forge-sandbox:latest`. Embedded Qdrant: ONE client per path per process.
