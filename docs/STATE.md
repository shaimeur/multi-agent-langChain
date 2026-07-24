<!-- The mutable progress ledger. Rewritten in full at every /checkpoint, never
     appended to. Keep it under 80 lines. Nothing goes under "Done (verified)"
     unless its DoD command actually ran and passed this or a named session.
     GOALS.md is the durable plan; this file is where you are in it. -->

Last updated : 2026-07-24
Roadmap day  : D7 done (DoD met) · D8 (Tester agent + repair loop) is next
Branch       : main
Last commit  : 753f314 [D7] Sandbox service — hardened container executor, structured ExecutionReport

## Done (verified)
- [x] D1 foundations · D2 AST ingestion (617 chunks) · D3 hybrid retrieval · Grounded-RAG (D5-pre)
- [x] **D4 RAG eval + config freeze** — span-overlap metric, 42-pair golden set, `evals/run_ablation.py`.
      **MiniLM FROZEN** (R@10 0.905 vs BGE 0.857); reranker OFF. docs/evaluation.md D4, notebook 01
- [x] **D5 LangGraph skeleton + memory** — `core/state.py`, `core/agents/` (supervisor/retriever/
      answer/summarize), `core/graph.py` + AsyncSqliteSaver (thread_id=session_id). `forge ask --session`
      = graph; bare `ask` = direct path. **Restart proven offline** (test_graph.py)
- [x] **D6 Planner + Editor** — models.py schemas; `core/workspace.py` (per-session git worktree,
      realpath-guarded); `tools/patch.py` (structured edits → diff → `git apply --check`);
      `core/agents/planner.py` (grounding enforced in code); `core/agents/editor.py` (never writes disk)
- [x] **D7 Sandbox service — DoD MET** (2026-07-24). `docker/sandbox.Dockerfile` (160 MB, non-root
      uid 1000, pip removed); `sandbox/runner.py` (ephemeral container per run + documented fallback);
      `sandbox/report.py` (pytest/ruff parsers); `sandbox/tools.py` (run_pytest/run_python/run_linter
      as LangChain tools); `ExecutionReport`+`Isolation`+`ExecutionOutcome` in models.py;
      **docs/limitations.md** (new, L2 deliverable).
      DoD — `pytest tests/test_sandbox.py -k "structured_report or infinite_loop or
      survives_a_runaway or fork_bomb or egress"` → **8 passed, 2 skipped, exit 0**.
      Full — `CACHE_MODE=replay uv run pytest` → **206 passed, 5 skipped, exit 0**.
      Lint — `ruff check src tests` → **exit 0**
- [x] Continuity system — .claude/ hooks, STATE.md, /checkpoint, CLAUDE.md

## In progress
- D8 Tester agent + repair loop. NEXT: `core/agents/tester.py` (SANDBOX_ENGINEER — writes the
  **failing** regression test first for a bug fix) → `implement_loop` subgraph Editor→Tester→
  Reviewer(stub)→Editor capped by `max_iterations_per_step` → `RevisionRequest` built from
  `ExecutionReport.failures` (evidence, not prose) → seed `evals/swe_mini/` with 4 bugs + hidden suite
  → notebook 02 trace. **DoD: a deliberately broken function repaired autonomously in <3 iterations.**
  The sandbox half is done and green; the LLM half is B2-gated (see below).

## Blocked / open decisions
- **B2 — no cloud key (no `.env`).** Now the critical path: D8's tester/reviewer are LLM work and a
  mistral-on-CPU graph turn is impractically slow with the parent-expanded pack. **Verify a Gemini or
  Groq key before starting D8.** D7 was infra and needed none.
- B3 — `qwen2.5-coder:7b` unpulled (only mistral). O1 React vs Streamlit (descope §2) — ASK THE HUMAN.
  O2 C6 "Tools AND MCP" wording. D1 compose DoD not re-run — ? unverified.
- **NEW O3 — `make lint` was already red before D7**: `src/forge/rag/embed.py` and `tests/test_graph.py`
  fail `ruff format --check` (pre-existing, untouched by D7). One `make fmt` fixes both; left alone so
  the D7 commit stays about D7. Decide whether to fix in a separate `chore(fmt)` commit.

## Do not redo
- **The sandbox is built** (D7). Container flags verified applied via `docker inspect` on a live
  container (all 15). **`RLIMIT_DATA`, not `RLIMIT_AS`**, in the fallback — measured: ruff SIGABRTed
  8/10 runs under RLIMIT_AS, 0/10 under RLIMIT_DATA, which still refuses a 1 GB alloc. Output is
  **head**-truncated at 64 KB (tailing means pulling 10 GB through the socket), so the **exit code is
  the authority**, never the parsed counts. Compose deliberately does **not** mount docker.sock
  (root-equivalent) — limitations.md §5. Container overhead measured at ~85 ms/run.
- **The change subsystem is built** (D6): worktree not copy; diff built from structured edits and
  validated with `git apply --check`; planner grounding enforced in code; editor never writes disk.
- **The graph is built** (D5). prefer_implementation wired live in the graph retriever; direct
  answer_question path unchanged (fixture safety). AsyncSqliteSaver at `langgraph.checkpoint.sqlite.aio`.
- **MiniLM FROZEN**, reranker OFF (D4). sqlparse @0d24023; Postgres/8-svc/monorepo rejected; torch CPU pin.

## Notes for the next session
- Commits OMIT the Claude co-author trailer (author Shaimeur). `*.sqlite*`, `evals/results/` gitignored.
- `src/forge/sandbox/` and `guardrails/` are security-sensitive (CLAUDE.md): flag any flag/cap/
  path-allowlist change before making it. The 5 skipped tests are container-only assertions under the
  fallback param — that is correct, not a gap in coverage.
- `make sandbox-image` builds `forge-sandbox:latest`. Without it the sandbox silently uses the fallback
  (visible as `ExecutionReport.isolation`), so rebuild it on a fresh clone before demoing hardening.
- Embedded Qdrant: ONE client per path per process. Run /checkpoint before stopping.
- **Untracked, predates D7**: `data/fixtures/llm/5538233c22e6d940.json` — a real ollama grounded-answer
  replay fixture recorded 2026-07-24T00:14 (a D6-era run). Left uncommitted deliberately, not lost.
  Commit it if that recording should be part of the offline demo set; delete it if it was a stray.
