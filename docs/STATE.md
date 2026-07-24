<!-- The mutable progress ledger. Rewritten in full at every /checkpoint, never
     appended to. Keep it under 80 lines. Nothing goes under "Done (verified)"
     unless its DoD command actually ran and passed this or a named session.
     GOALS.md is the durable plan; this file is where you are in it. -->

Last updated : 2026-07-24
Roadmap day  : D9 DoD met · Sprint 2 (D5–D9) complete · D10 (Guardrails) is next
Branch       : main
Last commit  : 98005dd [D9] Reviewer + human-in-the-loop — five points, two gates, headless end to end

## Done (verified)
- [x] D1 foundations · D2 AST ingestion (617 chunks) · D3 hybrid retrieval · Grounded-RAG (D5-pre)
- [x] **D4 RAG eval + config freeze** — MiniLM FROZEN (R@10 0.905 vs BGE 0.857); reranker OFF
- [x] **D5 LangGraph + memory** — `core/graph.py` + AsyncSqliteSaver. Restart proven offline
- [x] **D6 Planner + Editor** — worktree, `git apply --check`, grounding enforced in code
- [x] **D7 Sandbox** — ephemeral container per run + documented fallback; `docs/limitations.md`.
      All 15 container flags verified via `docker inspect`
- [x] **D8 Tester + repair loop** — regression-test-first, `RevisionRequest` from the report,
      `evals/swe_mini/` (4 seeded bugs + hidden tests). Broken function repaired in **2 iterations**
- [x] **D9 Reviewer + HITL — DoD MET** (2026-07-24). `core/agents/reviewer.py` (5 fixed points, each
      a boolean + justification + who decided it; **3 of 5 never reach a model** — `ReviewJudgement`
      has no field for grounding/tests/security); `core/approval.py` (`interrupt()` at both §5.5
      gates, defensive resume parsing, loop-pathology escalation); `core/loop.py` `build_change_graph`;
      `core/checkpoint.py` (strict serde, see below).
      DoD — `test_the_full_graph_runs_headless_through_both_approval_points`: pause 1 plan_approval
      (nothing on disk) → pause 2 patch_approval (still nothing on disk) → applied, green.
      Full — `CACHE_MODE=replay uv run pytest` → **265 passed, 5 skipped, exit 0**;
      `ruff check src tests evals` → exit 0
- [x] Continuity system — .claude/ hooks, STATE.md, /checkpoint, CLAUDE.md

## In progress
- D10 Guardrails + policy engine — *security-sensitive; `src/forge/guardrails/` is still an empty
  stub*. NEXT: `sentinel_in` / `sentinel_out` as **deterministic** nodes wrapping the graph (cahier
  §4/S — explicitly NOT a conversational agent: "un contrôle de sécurité avec lequel on peut négocier
  n'est pas un contrôle de sécurité") → prompt-injection detection on retrieved chunks (the demo
  plants a poisoned comment, §15.6) → the **guardrail event log** (a never-cut item) exposed at
  `GET /v1/guardrails/events` → path-policy engine formalising what `Workspace.resolve` already does
  → `evals/security/` adversarial suite. **C5 needs `uv run pytest evals/security -q` green AND
  `curl .../v1/guardrails/events | jq length` > 0.** Read GOALS.md D10 before starting.
- **Carried from D8**: `notebooks/02_agent_traces.ipynb` (L3 part 2). Still deferred — worth far more
  against a real model than a scripted one. C2 depends on it.

## Blocked / open decisions
- **B2 — no cloud key (`.env` has none; a hook blocks reading it — check booleans via
  `Settings.google_api_key`).** Only `mistral:latest` pulled. Everything D6–D9 is **mechanism-proven
  with scripted models**; no real model has ever driven the planner, editor, tester or reviewer.
  Specifically unproven: swe_mini repair rate, and the reviewer's two *judged* points. D10 is
  deterministic and NOT blocked — but a real-model run should happen before D14's demo.
- **NEW O4 — reviewer/editor model family.** §4/A5 wants different families; FORGE splits them by
  role (CODER vs REASONER), which under one provider is the same model.
  `shares_family_with_editor()` reports it. Decide at D14 whether to run two providers.
- B3 — `qwen2.5-coder:7b` unpulled. O1 React vs Streamlit (descope §2) — ASK THE HUMAN.
  O2 C6 "Tools AND MCP" wording. D1 compose DoD not re-run — ? unverified.
- **O3 — `make lint` red since before D7**: `src/forge/rag/embed.py`, `tests/test_graph.py` fail
  `ruff format --check` (pre-existing, untouched). One `make fmt` fixes both.

## Do not redo
- **Review + HITL are built** (D9). The patch gate is **between** `editor` (builds, dry-runs, never
  writes) and `apply` (the only writer) — that ordering is why rejection needs no rollback. The
  planner's exits are parameters so no caller can wire a graph that bypasses the plan gate.
- **The checkpoint serde is strict** (`core/checkpoint.py`): allowlist scoped to `forge.models` +
  `Budget`. Default LangGraph rebuilds *any* named type — a code-execution vector for anyone who can
  write `checkpoints.sqlite`. Use `sqlite_checkpointer()` / `MemorySaver(serde=forge_serde())`,
  never a bare saver, or the warnings and the hole come back.
- **The repair loop** (D8): reviewer routes on the **exit code**; `evals/run_swe_mini.py --verify`
  needs no model and catches a target-repo bump invalidating the benchmark.
- **The sandbox** (D7): `RLIMIT_DATA` not `RLIMIT_AS` (measured: ruff SIGABRTed 8/10 under AS).
  Output **head**-truncated at 64 KB → the **exit code is the authority**. Compose does **not** mount
  docker.sock (root-equivalent) — limitations.md §5.
- **MiniLM FROZEN**, reranker OFF (D4). sqlparse @0d24023. AsyncSqliteSaver at `...sqlite.aio`.

## Notes for the next session
- Commits OMIT the Claude co-author trailer (author Shaimeur). `pyproject.toml` sets
  `pythonpath = ["."]` so tests import `evals`; isort knows `forge` + `evals` as first-party.
- `src/forge/sandbox/` and `guardrails/` are security-sensitive (CLAUDE.md): flag any flag/cap/
  path-allowlist change first. The 5 skipped tests are container-only assertions under the fallback
  param — correct, not a coverage gap.
- `make sandbox-image` builds `forge-sandbox:latest`; without it the sandbox silently uses the
  fallback (visible as `ExecutionReport.isolation`). Embedded Qdrant: ONE client per path per process.
