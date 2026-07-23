<!-- The mutable progress ledger. Rewritten in full at every /checkpoint, never
     appended to. Keep it under 80 lines. Nothing goes under "Done (verified)"
     unless its DoD command actually ran and passed this or a named session.
     GOALS.md is the durable plan; this file is where you are in it. -->

Last updated : 2026-07-24
Roadmap day  : D5 done (DoD met) · D6 (Planner + Editor) is next
Branch       : main
Last commit  : e300937 [D5] LangGraph skeleton + memory — checkpointed multi-turn Q&A, restart survives

## Done (verified)
- [x] D1 foundations — config, fixture cache, LLM factory, /v1/health, compose, ADR-001/002
- [x] D2 AST ingestion — walker, chunker, BM25, Qdrant. sqlparse: 59 files → 617 chunks
- [x] D3 retrieval — hybrid dense+sparse, RRF, ripgrep+AST tools, `forge search`
- [x] Grounded-RAG service (D5-pre) — rag/answer.py: retrieve → answer → verify [n] citations
- [x] **D4 RAG eval + config freeze** — span-overlap metric (`forge.evaluation`), 42-pair golden set,
      `evals/run_ablation.py`. **MiniLM FROZEN** (R@10 0.905 vs BGE 0.857); reranker OFF (lowers nDCG).
      Notebook 01. Table in docs/evaluation.md D4
- [x] **D5 LangGraph skeleton + memory — DoD MET** (2026-07-24). `core/state.py` (ForgeState,
      merge_chunks, Budget), `core/agents/` (supervisor routing+budget guard, retriever with D4
      pipeline live + differential pack, answer, summarize), `core/graph.py` + AsyncSqliteSaver
      (thread_id=session_id). `forge ask --session` = graph (astream + Rich timeline); bare `ask` =
      direct path. `scripts/c4_restart_resume.sh`. **Restart proven offline**:
      `pytest tests/test_graph.py::test_session_survives_a_process_restart`. `uv run pytest` → **143 passed**
- [x] Continuity system — .claude/ hooks, STATE.md, /checkpoint, CLAUDE.md

## In progress
- D6 Planner + Editor. NEXT (LLM-free scaffolding first, since B2 gates the model parts): models.py
  schemas (`CitationRef`, `PlanStep`, `ChangePlan`, `Patch`, `PatchSet`) → `core/workspace.py`
  (per-session git worktree under workspace_root) → `tools/patch.py` (`apply_patch_dryrun` via
  `git apply --check` in the worktree). THEN `core/agents/planner.py`
  (`with_structured_output(ChangePlan)`; a step whose evidence does not resolve into the ContextPack
  is rejected) + `needs_more_context` → `Command(goto="retriever")` with a re-entry cap +
  `core/agents/editor.py` (one step → a PatchSet, never writes disk).
  **DoD: a real change request produces a plan and a patch that `git apply --check` accepts.**

## Blocked / open decisions
- **B2 — no cloud key (no `.env`).** Now GATES D6's Editor: patch generation needs a real CODER model;
  mistral 7B won't drive Planner/Editor/Reviewer. Build D6's non-LLM scaffolding first, then verify a
  Gemini/Groq key before wiring planner/editor. D5's graph runs on mistral via LLM_PROVIDER=ollama with
  the router/reasoner overridden to mistral (see scripts/c4_restart_resume.sh header).
- B3 — `qwen2.5-coder:7b` still unpulled (only mistral). Needed if the local path drives the coder role.
- O1 React vs Streamlit (descope §2) — blocks D13 + the pyproject `ui` extra. ASK THE HUMAN.
- O2 C6 "Tools AND MCP" vs cut list — fix the criterion wording before the first cut.
- D1 compose DoD (`docker compose up`) not re-run — ? unverified.

## Do not redo
- **The graph is built** (state/nodes/graph/checkpointer). `forge ask --session X` = graph path
  (checkpointed, multi-turn); bare `forge ask` = the direct path, LEFT UNCHANGED to protect the
  committed mistral fixture. AsyncSqliteSaver import: `langgraph.checkpoint.sqlite.aio`.
- **prefer_implementation is wired live in the GRAPH retriever** (settings default True) — the D4-staged
  lever, done. The direct answer_question path deliberately does NOT use it (fixture safety).
- Restart survival is proven OFFLINE (test_graph.py) with fakes + temp SQLite — the authoritative C4
  proof; the shell script is the live demo. Don't rebuild the proof.
- **MiniLM FROZEN**, reranker OFF (D4) — don't re-argue. sqlparse @0d24023, Postgres/8-svc/monorepo
  rejected, torch CPU pin. Grounded-RAG is the direct path; D6-D9 build Planner/Editor/tester/reviewer.

## Notes for the next session
- Commits OMIT the Claude co-author trailer (author Shaimeur). Do not re-add it.
- `*.sqlite*` and `evals/results/` are gitignored. A live c4 mistral run this session recorded graph
  fixtures under data/fixtures/llm/ (record/replay for `forge ask --session` offline) — check if worth
  committing next session. Embedded Qdrant: ONE client per path per process. Run /checkpoint before stopping.
