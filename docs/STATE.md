<!-- The mutable progress ledger. Rewritten in full at every /checkpoint, never
     appended to. Keep it under 80 lines. Nothing goes under "Done (verified)"
     unless its DoD command actually ran and passed this or a named session.
     GOALS.md is the durable plan; this file is where you are in it. -->

Last updated : 2026-07-23
Roadmap day  : D4 done (DoD met) · Sprint 1 complete · D5 (LangGraph skeleton + memory) is next
Branch       : main
Last commit  : ca9237a [D4] RAG evaluation + config freeze — §13.1 ablation, MiniLM confirmed

## Done (verified)
- [x] D1 foundations — config, fixture cache, LLM factory, /v1/health, compose, ADR-001/002
- [x] D2 AST ingestion — walker, chunker, BM25, Qdrant. sqlparse: 59 files → 617 chunks, 51.6 s
- [x] D3 retrieval — hybrid dense+sparse, RRF, ripgrep+AST tools, `forge search`. Baseline (exact-id)
      Recall@10 0.400 via `evals/run_retrieval.py`
- [x] Grounded-RAG service (D5-pre) — rag/answer.py: retrieve → answer from numbered snippets → verify
      every [n] citation in code. `forge ask`, POST /v1/ask. Runs on local Ollama+mistral
- [x] **D4 RAG eval + config freeze — DoD MET** (2026-07-23). Span-overlap metric (`forge.evaluation`);
      golden set 15→42 pairs; `evals/run_ablation.py` fills the §13.1 table; parent-doc packer
      (rag/pack.py), harness-only reranker (rag/rerank.py), naive chunker, prefer_implementation.
      Findings: AST 0.655→0.857 R@10; prefer-impl fixes test-pollution to 0.869; +parent-exp 0.905.
      **MiniLM FROZEN** (R@10 0.905 vs BGE 0.857, 37× cheaper index); reranker OFF (2.6 s p95, lowers
      nDCG). Notebook 01 (charts). `uv run pytest` → **134 passed**. Table in docs/evaluation.md D4
- [x] Continuity system — .claude/ hooks, STATE.md, /checkpoint, CLAUDE.md

## In progress
- D5 LangGraph skeleton + memory. NEXT (in order): `core/state.py` (ForgeState, `merge_chunks` reducer
  dedup by chunk_id, Budget) → `core/agents/supervisor.py` (`with_structured_output(RouteDecision)`,
  `Command(goto=...)`, budget guard) → `core/agents/retriever.py` wrapping D3/D4 (differential pack on
  re-entry) → `core/graph.py` (StateGraph + AsyncSqliteSaver, thread_id=session_id) → sliding-summary
  node → `forge ask` on the graph with astream → `scripts/c4_restart_resume.sh` (the C4 proof).
  **DoD: multi-turn grounded Q&A with citations, surviving a process restart mid-session.**
- Staged from D4 (do on D5): wire `prefer_implementation=True` into live answer.py + **re-record the
  mistral fixture** (proven in harness, defaulted in config, not yet flipped under the committed
  fixture). Also RAGAS/generation metrics — deferred, needs a cloud key.

## Blocked / open decisions
- **B2 — no cloud key still** (no `.env`; local mistral only). Verify a Gemini/Groq key BEFORE D5 —
  the agent loop is blocked behind it; mistral 7B won't drive Planner/Editor/Reviewer. Risk watchlist:
  half a day, hard stop, before anything else on D5.
- B3 — `qwen2.5-coder:7b` still unpulled (only mistral). Needed if the local path drives the coder role.
- O1 React vs Streamlit (descope §2) — blocks D13 + the pyproject `ui` extra. ASK THE HUMAN.
- O2 C6 "Tools AND MCP" vs cut list — fix the criterion wording before the first cut.
- D1 compose DoD (`docker compose up` → qdrant+api healthy) not re-run this session — ? unverified.

## Do not redo
- **MiniLM FROZEN on D4's ablation** (R@10 0.905 vs BGE 0.857 on the full pipeline, 37× cheaper index,
  11× cheaper query). BGE kept configurable for a GPU box. The D3 "MiniLM too weak" worry is RESOLVED —
  it was a weak *pipeline*, not a weak *embedder*. Don't re-argue the embedder or reopen from throughput.
- **Reranker OFF** — measured 2.6 s p95 AND *lowers* nDCG on code (general web-search model). Built for
  the harness only (RERANK_ENABLED). Don't enable it.
- Eval metric is **span-overlap** (path,lines), not exact chunk-id. Golden set = 42 pairs keyed to AST
  chunk_ids. `code_naive`/`code_bge` are gitignored artifacts (rebuild: `evals/build_ablation_indexes.py`).
- Prior calls stand: sqlparse @0d24023 (ADR-003), Postgres/8-svc/monorepo rejected, torch CPU pin.
  The grounded-RAG service is the DIRECT path; D5-D9 still build the graph around it (C3 not fully
  closed — no evals/test_citations_resolve.py yet; don't tick D5 boxes because `forge ask` exists).

## Notes for the next session
- Commits OMIT the Claude co-author trailer (author Shaimeur). Do not re-add it.
- `evals/results/` is gitignored (latency churns); curated numbers in docs/evaluation.md, charts baked
  into notebooks/01_rag_evaluation.ipynb. `notebooks` pyproject extra added (matplotlib/pandas/nbconvert).
- Embedded Qdrant allows ONE client per path per process — build once and inject. Local profile:
  `cp .env.local.example .env` (the hook blocks writing `.env` directly). Run /checkpoint before stopping.
