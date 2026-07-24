<!-- The mutable progress ledger. Rewritten in full at every /checkpoint, never
     appended to. Keep it under 80 lines. Nothing goes under "Done (verified)"
     unless its DoD command actually ran and passed this or a named session.
     GOALS.md is the durable plan; this file is where you are in it. -->

Last updated : 2026-07-24
Roadmap day  : D6 done (DoD met) · D7 (Sandbox service — hardest infra day) is next
Branch       : main
Last commit  : 5b57616 [D6] Planner + Editor — grounded ChangePlan, structured PatchSet, git apply --check

## Done (verified)
- [x] D1 foundations · D2 AST ingestion (617 chunks) · D3 hybrid retrieval · Grounded-RAG (D5-pre)
- [x] **D4 RAG eval + config freeze** — span-overlap metric, 42-pair golden set, `evals/run_ablation.py`.
      **MiniLM FROZEN** (R@10 0.905 vs BGE 0.857); reranker OFF. docs/evaluation.md D4, notebook 01
- [x] **D5 LangGraph skeleton + memory** — `core/state.py`, `core/agents/` (supervisor/retriever/
      answer/summarize), `core/graph.py` + AsyncSqliteSaver (thread_id=session_id). `forge ask --session`
      = graph; bare `ask` = direct path. **Restart proven offline** (test_graph.py)
- [x] **D6 Planner + Editor — DoD MET** (2026-07-24). models.py schemas (CitationRef/PlanStep/ChangePlan/
      Patch/PatchSet); `core/workspace.py` (per-session git worktree, realpath-guarded); `tools/patch.py`
      (build diff from structured edits → `git apply --check`); `core/agents/planner.py` (grounding in
      code — ungrounded steps dropped; needs_more_context→retriever, capped); `core/agents/editor.py`
      (step → PatchSet, never writes disk). Proof: test_change.py. `uv run pytest` → **161 passed**
- [x] Continuity system — .claude/ hooks, STATE.md, /checkpoint, CLAUDE.md

## In progress
- D7 Sandbox service — *hardest infra day; security-sensitive, do not relax a flag without flagging*.
  NEXT (all LLM-free — NOT B2-blocked): `docker/sandbox.Dockerfile` (python+pytest+ruff, non-root,
  minimal) → `sandbox/runner.py` (ephemeral container per run via Docker SDK: `--network=none`,
  read-only root, writable mount = the worktree only, `--memory=512m --cpus=1 --pids-limit=128`, hard
  timeout, stdout truncation at 64 KB) → `ExecutionReport` model + pytest output parser →
  `run_pytest`/`run_python`/`run_linter` as LangChain tools returning `ExecutionReport` → the documented
  `subprocess`+`resource.setrlimit`+timeout fallback when the Docker socket is absent (write the gap into
  docs/limitations.md) → hardening tests (infinite loop killed, fork bomb contained, egress refused,
  10 GB stdout truncated). Config already has sandbox_* caps.
  **DoD: pytest runs in the sandbox and returns a structured report; a deliberate infinite loop is
  killed cleanly and the API stays up.** Verify Docker is up first (descope: 29.6.2 working).

## Blocked / open decisions
- **B2 — no cloud key (no `.env`).** Does NOT block D7 (infra). It gates D6's real-model patch *quality*
  and D8's tester/reviewer LLM parts. Verify a Gemini/Groq key before D8. D5's graph runs on mistral via
  LLM_PROVIDER=ollama + router/reasoner overridden to mistral (see scripts/c4_restart_resume.sh header) —
  but a full mistral graph turn is impractically slow on CPU with the parent-expanded pack (a live c4 run
  this session did not finish turn 1; the offline test is the authoritative proof).
- B3 — `qwen2.5-coder:7b` unpulled (only mistral). O1 React vs Streamlit (descope §2) — ASK THE HUMAN.
  O2 C6 "Tools AND MCP" wording. D1 compose DoD not re-run — ? unverified.

## Do not redo
- **The change subsystem is built** (D6): workspace = a git worktree (not a copy); patch.py builds the
  diff from structured search/replace edits and validates with `git apply --check` (don't have the model
  emit diff syntax); planner grounding is enforced in code. Editor never writes disk.
- **The graph is built** (D5). prefer_implementation is wired live in the graph retriever (default True);
  the direct answer_question path is unchanged (fixture safety). AsyncSqliteSaver at
  `langgraph.checkpoint.sqlite.aio`. Restart proof is offline.
- **MiniLM FROZEN**, reranker OFF (D4). sqlparse @0d24023; Postgres/8-svc/monorepo rejected; torch CPU pin.

## Notes for the next session
- Commits OMIT the Claude co-author trailer (author Shaimeur). `*.sqlite*`, `evals/results/` gitignored.
- `src/forge/sandbox/` is security-sensitive (CLAUDE.md): flag any flag/cap/path-allowlist change first.
- Embedded Qdrant: ONE client per path per process. Run /checkpoint before stopping.
