<!-- The mutable progress ledger. Rewritten in full at every /checkpoint, never
     appended to. Keep it under 80 lines. Nothing goes under "Done (verified)"
     unless its DoD command actually ran and passed this or a named session.
     GOALS.md is the durable plan; this file is where you are in it. -->

Last updated : 2026-07-23
Roadmap day  : D3 done (DoD met) · D4 is next
Branch       : main
Last commit  : (D3 retrieval commit lands on top of 8f884dd this session)

## Done (verified)
- [x] D1 foundations — config, fixture cache, LLM factory, /v1/health, forge config/index,
      4-service compose, CPU-pinned Dockerfile, ADR-001/002 — `uv run pytest` → 68 passed (2026-07-23)
- [x] D2 AST ingestion — walker, tree-sitter chunker, BM25 sparse, Qdrant store. **DoD now MET**:
      sqlparse indexed, 59 files → 617 chunks, 51.6 s, 3.3 MB (docs/evaluation.md)
- [x] D3 retrieval — rag/retrieve.py (dense+sparse over named vectors, RRF fusion, language/path
      filters, identifier-query gate), tools/ripgrep.py + tools/ast_symbols.py, `forge search`,
      15-pair golden set. `evals/run_retrieval.py` → Recall@10 0.400 / Hit@10 0.400 / MRR 0.207.
      `uv run pytest` → 93 passed (2026-07-23)
- [x] Continuity system — .claude/ hooks, STATE.md, /checkpoint, CLAUDE.md (2026-07-23)

## In progress
- D4 RAG evaluation + config freeze — *never sacrifice this day*. NEXT CONCRETE ACTION: build
  `evals/run_ablation.py` emitting the §13.1 table over all 5 configs; add parent-document expansion
  + the token-budget `ContextPack` packer; wire the cross-encoder reranker behind `RERANK_ENABLED`
  into the harness only (off live, descope §3); grow the golden set to 30–40 pairs. Then RUN it and
  decide MiniLM vs BGE-M3 on Recall@10 + nDCG@10 — the 0.400 baseline says MiniLM is likely too weak,
  so this is where BGE-M3 and/or the reranker earn their cost. Freeze the winner in config.py; write
  the decision + the reranker's measured cost into docs/evaluation.md; notebook 01.
- Also on D4: chase the **test-corpus pollution** finding — `test_*` functions outrank the code they
  test (see docs/evaluation.md D3). A path/language filter preferring implementation is one lever.

## Blocked / open decisions
- B1  RESOLVED — target repo is sqlparse 0.5.5 @ 0d24023, ADR-003. data/target cloned (gitignored).
- B2  No .env, no LLM provider key verified against a live quota. Blocks every agent day (D5+) and the
      LLM query-rewrite branch of the D3 gate. UNBLOCK BEFORE D5 (risk watchlist: half a day, hard stop).
- B3  Ollama holds mistral:latest only; config routes all roles to qwen2.5-coder:7b (~4.5 GB, not pulled).
- O1  React vs Streamlit (descope §2) — blocks D13 and the pyproject `ui` extra. ASK THE HUMAN.
- O2  C6 requires "Tools AND MCP" but the cut list makes MCP cuttable. Fix C6 wording before the first cut.
- D1 compose DoD (`docker compose up` → qdrant + api healthy) not re-run this session — ? unverified today.

## Do not redo
- BGE-M3 rejected as default on CPU throughput (36×). Kept configurable. D4's ablation MAY buy it back
  on quality — the 0.400 Recall@10 baseline is exactly that trigger. Don't re-argue from throughput alone.
- sqlparse as target repo, pinned @0d24023 (B1 closed, ADR-003). Do not re-choose.
- Postgres checkpointer, 8-service compose, 3-package monorepo — decided against (descope §1/§5/§12.4).
- torch default wheel — pins to the CPU index (2.7 GB of unusable CUDA otherwise). Do not unpin.

## Notes for the next session
- Commits now OMIT the Claude co-author trailer, per the human's instruction (2026-07-23). Author is
  Shaimeur; do not re-add `Co-Authored-By: Claude` / `Claude-Session:` lines.
- README is stale: claims "22 tests / Day 2", actual is 93 tests with D3 shipped. Fix on the next
  commit that touches README.md.
- Embedded Qdrant allows ONE client handle per path per process. Build once and inject the client (as
  evals/run_retrieval.py and hybrid_search's injectable params do). The graph/API (D5+) should share
  one client or use the Qdrant server (the compose default, QDRANT_URL set).
- Run /checkpoint before you stop.
