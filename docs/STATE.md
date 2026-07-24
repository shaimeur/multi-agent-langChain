<!-- The mutable progress ledger. Rewritten in full at every /checkpoint, never
     appended to. Keep it under 80 lines. Nothing goes under "Done (verified)"
     unless its DoD command actually ran and passed this or a named session.
     GOALS.md is the durable plan; this file is where you are in it. -->

Last updated : 2026-07-24
Roadmap day  : D12 DoD met (`forge review` carried) · D13 (Web UI) is next — **O1 blocks it**
Branch       : main
Last commit  : b37a52d [D12] §11 route table, SSE streaming, and `forge fix` end to end

## Done (verified)
- [x] D1 foundations · D2 AST ingestion (617 chunks) · D3 hybrid retrieval · D4 RAG eval
      (**MiniLM FROZEN**, reranker OFF) · D5 LangGraph + memory (restart proven offline)
- [x] **D6 Planner + Editor** — worktree, `git apply --check`, grounding enforced in code
- [x] **D7 Sandbox** — container per run + documented fallback; `docs/limitations.md`; 15 flags
      verified via `docker inspect`
- [x] **D8 Tester + repair loop** — regression-test-first; `evals/swe_mini/` (4 bugs + hidden
      tests); broken function repaired in **2 iterations** (scripted model)
- [x] **D9 Reviewer + HITL** — 5 fixed points, **3 of 5 never reach a model**; `interrupt()` at both
      §5.5 gates; strict checkpoint serde (`core/checkpoint.py`)
- [x] **D10 Guardrails** — `guardrails/` (events log, policy, injection, both sentinels), wired live
- [x] **D11 Red team** — `evals/security/`, **32/32 attacks mitigated**, CI workflow. **C5 CLOSED**
- [x] **D12 API + CLI — DoD MET** (2026-07-24). All §11 routes; `api/sessions.py` (worktree +
      counters), `api/streaming.py` (SSE, five typed frame kinds, always a terminal frame);
      `/v1/metrics`; `forge fix` with the live timeline and both gates as prompts;
      `scripts/sse_smoke.sh`. **C8 CLOSED.**
      Full — `CACHE_MODE=replay uv run pytest` → **340 passed, 5 skipped, exit 0**;
      `./scripts/sse_smoke.sh` → **exit 0**; `evals/security` → 32/32; `ruff` → exit 0
- [x] Continuity system — .claude/ hooks, STATE.md, /checkpoint, CLAUDE.md

## In progress
- D13 Web UI — **blocked on O1 (React vs Streamlit, descope §2 is OPEN). ASK THE HUMAN FIRST.**
  Whichever wins, the API is ready: sessions, SSE, approve, history, metrics all exist and are
  tested. C7 wants the §15.6 four-minute script recorded to `docs/demo.mp4` — that needs B2 too.
- **Carried**: `forge review` (D12 — `forge fix` already renders the checklist; standalone wants a
  real model) · injection tier 2 (O5, needs a human call) · `notebooks/02_agent_traces.ipynb`
  (L3 part 2, C2 depends on it) · `forge tools` listing 10 (C6, but settle O2's wording first).

## Blocked / open decisions
- **B2 — no cloud key** (`.env` has none; a hook blocks reading it — check booleans via
  `Settings.google_api_key`). Only `mistral:latest`. D6–D9 and D12's graph path are
  **mechanism-proven with scripted models**; no real model has driven planner/editor/tester/reviewer,
  and swe_mini has never run for real. **Blocks C2, C3, C7 and the D14 demo. This is now the single
  biggest risk to the grade.**
- **O1 — React vs Streamlit** (descope §2, still OPEN). D13 cannot start without it.
- **O5 — injection tier 2** not built (tier 1 only). Argued in limitations.md §7; accepting it
  properly needs a `descope-v1.md` entry and that file is frozen → needs the human.
- **O4 — reviewer/editor model family**: split by role (CODER vs REASONER), one model under one
  provider. `shares_family_with_editor()` reports it.
- B3 — `qwen2.5-coder:7b` unpulled. **O2** — C6 "Tools AND MCP" wording. D1 compose DoD not re-run.
  **O3** — `make lint` red since before D7 (`rag/embed.py`, `tests/test_graph.py` fail
  `ruff format --check`); one `make fmt` fixes it.
- **Gates: 2 of C1–C10 closed (C5, C8) at D12 of D15.** C1 is still nearly free — 8 agents exist, it
  only wants `tests/test_agents.py -k distinct`, no model needed.

## Do not redo
- **`build_llm` installs a process-global LangChain cache.** `reset_llm_cache()` + an autouse
  conftest fixture undo it between tests. Without that, any test touching a real provider routes
  every later `FakeListChatModel` through the fixture store → FixtureMiss in an unrelated file.
- **The API is built** (D12). SSE frames are **CRLF**-terminated (bit the smoke script). Every
  stream ends with `done`/`error`/`interrupt`. `set_graph_factory()` is the test seam. History is
  read from the checkpointer, not memory, so it survives a restart.
- **Guardrails** (D10/D11): the event log **never raises** (both paths catch `Exception` — `_connect`
  fails on the filesystem first). `check_answer(answer, None)` = "verified upstream". Scanned chunks
  are **copied**. The security pass rate is *derived* from pytest outcomes — never hand-keep it.
- **Review + HITL** (D9): the patch gate sits *between* `editor` (never writes) and `apply`. Planner
  exits are parameters so no graph can bypass the plan gate. Use `sqlite_checkpointer()` /
  `MemorySaver(serde=forge_serde())`, never a bare saver.
- **Sandbox** (D7): `RLIMIT_DATA` not `RLIMIT_AS`. Output head-truncated → **exit code is the
  authority**. compose does **not** mount docker.sock. sqlparse @0d24023.

## Notes for the next session
- Commits OMIT the Claude co-author trailer (author Shaimeur). `pythonpath = ["."]`; isort
  first-party = `forge` + `evals`. `testpaths = ["tests"]`, so the security suite runs only when
  named — CI runs it as its own step. The 5 skipped tests are container-only under the fallback param.
- `sandbox/` + `guardrails/` are security-sensitive (CLAUDE.md): flag any flag/cap/allowlist change.
- `make sandbox-image` builds `forge-sandbox:latest`. Embedded Qdrant: ONE client per path per process.
