<!-- The mutable progress ledger. Rewritten in full at every /checkpoint, never
     appended to. Keep it under 80 lines. Nothing goes under "Done (verified)"
     unless its DoD command actually ran and passed this or a named session.
     GOALS.md is the durable plan; this file is where you are in it. -->

Last updated : 2026-07-24
Roadmap day  : D8 DoD met (1 task carried) · D9 (Reviewer + human-in-the-loop) is next
Branch       : main
Last commit  : dd0f66d [D8] Tester agent + repair loop — broken function repaired in 2 iterations

## Done (verified)
- [x] D1 foundations · D2 AST ingestion (617 chunks) · D3 hybrid retrieval · Grounded-RAG (D5-pre)
- [x] **D4 RAG eval + config freeze** — MiniLM FROZEN (R@10 0.905 vs BGE 0.857); reranker OFF.
      Span-overlap metric, 42-pair golden set, `evals/run_ablation.py`. docs/evaluation.md, notebook 01
- [x] **D5 LangGraph skeleton + memory** — `core/graph.py` + AsyncSqliteSaver (thread_id=session_id).
      `forge ask --session` = graph; bare `ask` = direct path. **Restart proven offline** (test_graph.py)
- [x] **D6 Planner + Editor** — `core/workspace.py` (per-session git worktree, realpath-guarded);
      `tools/patch.py` (structured edits → diff → `git apply --check`); planner grounding enforced in
      code; editor never writes disk
- [x] **D7 Sandbox service** — `docker/sandbox.Dockerfile` (160 MB, non-root); `sandbox/runner.py`
      (ephemeral container per run + documented fallback); `sandbox/report.py`; `sandbox/tools.py`
      (run_pytest/run_python/run_linter); **docs/limitations.md** (new, L2). All 15 container flags
      verified via `docker inspect`. DoD → 8 passed 2 skipped, exit 0
- [x] **D8 Tester + repair loop — DoD MET for the mechanism** (2026-07-24). `core/agents/tester.py`
      (regression writes the failing test first + records `regression_red`; verify runs the suite);
      `core/loop.py` (`implement_loop`, capped); `RevisionRequest` from the ExecutionReport;
      `apply_patchset` in tools/patch.py; `evals/swe_mini/` (4 seeded sqlparse bugs + hidden tests).
      DoD — broken function repaired in **2 iterations** (real worktree/git/pytest/sandbox, scripted
      model). `evals/run_swe_mini.py --verify` → **4/4 sound, exit 0**.
      Full — `CACHE_MODE=replay uv run pytest` → **229 passed, 5 skipped, exit 0**;
      `ruff check src tests evals` → exit 0
- [x] Continuity system — .claude/ hooks, STATE.md, /checkpoint, CLAUDE.md

## In progress
- D9 Reviewer + human-in-the-loop. NEXT: `core/agents/reviewer.py` — the fixed 5-point checklist,
  each point a boolean **plus** its justification; grounding checked **in code** via
  `ContextPack.supports()`, never the LLM's opinion. Then the `APPROVE`/`REVISE(feedback,
  target_step)` schema (D8 deliberately did NOT invent it — the stub returns
  `RevisionRequest | None`), `interrupt()` at plan approval and again before any patch touches
  disk, supervisor loop-pathology detection (Editor/Reviewer disagreeing 3× → escalate), and
  graceful budget exhaustion. **DoD: the full graph runs end to end headless with two human
  approval points.** Reviewer must be a different model family from the Editor — B2-gated.
- **Carried from D8**: `notebooks/02_agent_traces.ipynb` (L3 part 2). Deliberately deferred — it is
  an annotated trace and is worth far more against a real model than a scripted one.

## Blocked / open decisions
- **B2 — no cloud key (`.env` has none; a hook blocks reading it, use booleans via
  `Settings.google_api_key`).** Only `mistral:latest` is pulled. This is now **the** blocker: D9's
  reviewer needs a different model family from the editor, and D8's swe_mini benchmark has never been
  run against a real model. Everything through D8 is mechanism-proven with scripted models.
- B3 — `qwen2.5-coder:7b` unpulled. O1 React vs Streamlit (descope §2) — ASK THE HUMAN.
  O2 C6 "Tools AND MCP" wording. D1 compose DoD not re-run — ? unverified.
- **O3 — `make lint` red before D7, still red**: `src/forge/rag/embed.py` and `tests/test_graph.py`
  fail `ruff format --check` (pre-existing, untouched). One `make fmt` fixes both; kept out of the
  D7/D8 commits so they stay about D7/D8. Decide: separate `chore(fmt)` commit?

## Do not redo
- **The repair loop is built** (D8). The editor still never writes — `apply_patchset` is a separate
  step gated on `git apply --check`, into the throwaway worktree. The reviewer routes on the **exit
  code**, never on a model's reading of the transcript. `RevisionRequest` carries test ids + stderr,
  and a TIMEOUT is reported as a timeout, not as failing tests. `evals/run_swe_mini.py --verify`
  needs no model and is what catches a target-repo bump silently invalidating the benchmark.
- **The sandbox is built** (D7). `RLIMIT_DATA` not `RLIMIT_AS` in the fallback (measured: ruff
  SIGABRTed 8/10 under AS, 0/10 under DATA). Output **head**-truncated at 64 KB, so the **exit code is
  the authority**. Compose does **not** mount docker.sock (root-equivalent) — limitations.md §5.
- **The change subsystem** (D6) and **the graph** (D5) are built. AsyncSqliteSaver at
  `langgraph.checkpoint.sqlite.aio`. **MiniLM FROZEN**, reranker OFF (D4). sqlparse @0d24023.

## Notes for the next session
- Commits OMIT the Claude co-author trailer (author Shaimeur). `*.sqlite*`, `evals/results/`,
  `data/target/` gitignored. `pyproject.toml` now sets `pythonpath = ["."]` so tests can import `evals`.
- `src/forge/sandbox/` and `guardrails/` are security-sensitive (CLAUDE.md): flag any flag/cap/
  path-allowlist change first. The 5 skipped tests are container-only assertions under the fallback
  param — correct, not a coverage gap.
- `make sandbox-image` builds `forge-sandbox:latest`; without it the sandbox silently uses the
  fallback (visible as `ExecutionReport.isolation`). Embedded Qdrant: ONE client per path per process.
- **Untracked, predates D7**: `data/fixtures/llm/5538233c22e6d940.json` — a real ollama
  grounded-answer fixture (recorded 2026-07-24T00:14, D6-era). Left uncommitted deliberately: commit
  it if it belongs to the offline demo set, delete it if it was a stray.
