<!-- The mutable progress ledger. Rewritten in full at every /checkpoint, never
     appended to. Keep it under 80 lines. Nothing goes under "Done (verified)"
     unless its DoD command actually ran and passed this or a named session.
     GOALS.md is the durable plan; this file is where you are in it. -->

Last updated : 2026-07-23
Roadmap day  : D3 done (DoD met) · a runnable grounded-RAG service built ahead of D4 · D4 is next
Branch       : main
Last commit  : (D3 retrieval + grounded-RAG service commits land on top of 8f884dd this session)

## Done (verified)
- [x] D1 foundations — config, fixture cache, LLM factory, /v1/health, compose, ADR-001/002 (2026-07-23)
- [x] D2 AST ingestion — walker, chunker, BM25 sparse, Qdrant store. **DoD MET**: sqlparse indexed,
      59 files → 617 chunks, 51.6 s, 3.3 MB (docs/evaluation.md)
- [x] D3 retrieval — rag/retrieve.py (dense+sparse, RRF, filters, identifier gate), ripgrep + AST
      tools, `forge search`, 15-pair golden set → Recall@10 0.400 (evals/run_retrieval.py)
- [x] Grounded-RAG service (a slice of C3/D5, built ahead of D4 on request) — rag/answer.py:
      retrieve → answer from numbered snippets → **verify every [n] citation in code** vs the pack.
      `forge ask`, POST /v1/search, POST /v1/ask. Runs on local Ollama+mistral, no cloud key.
      Live: grounded answer + 4 verified citations in 194 s; replays offline in 25 s from the
      recorded fixture. `uv run pytest` → **100 passed** (2026-07-23)
- [x] Continuity system — .claude/ hooks, STATE.md, /checkpoint, CLAUDE.md (2026-07-23)

## In progress
- D4 RAG evaluation + config freeze — *never sacrifice this day*. NEXT: `evals/run_ablation.py` over
  the 5 §13.1 configs; parent-document expansion + token-budget ContextPack packer; reranker behind
  RERANK_ENABLED (harness only, off live); golden set to 30–40 pairs. Then RUN it and decide MiniLM
  vs BGE-M3 on Recall@10 + nDCG@10 — the 0.400 baseline says MiniLM is likely too weak. Freeze the
  winner in config.py; write the reranker's measured cost into docs/evaluation.md; notebook 01.
- Also on D4: the **test-corpus pollution** finding — `test_*` outranks the code it tests
  (docs/evaluation.md D3). A path/language filter preferring implementation is one lever.

## Blocked / open decisions
- B1  RESOLVED — sqlparse 0.5.5 @ 0d24023, ADR-003. data/target cloned (gitignored).
- B2  PARTIAL — `forge ask` now runs on local Ollama+mistral (no key) via .env.local.example. A
      hosted key (Gemini/Groq) is still needed for answer quality and for the D5+ agent loop; mistral
      7B will NOT drive Planner/Editor/Reviewer. Verify a cloud key before D5.
- B3  Only mistral:latest is pulled; it is now wired for the ask path. config's qwen2.5-coder:7b
      default is still unpulled — needed if the local path ever drives the coder role.
- O1  React vs Streamlit (descope §2) — blocks D13 and the pyproject `ui` extra. ASK THE HUMAN.
- O2  C6 requires "Tools AND MCP" but the cut list makes MCP cuttable. Fix C6 wording before the first cut.
- D1 compose DoD (`docker compose up` → qdrant + api healthy) not re-run this session — ? unverified today.

## Do not redo
- BGE-M3 rejected as default on CPU throughput (36×). Kept configurable. D4's ablation MAY buy it back
  on quality — the 0.400 Recall@10 baseline is exactly that trigger. Don't re-argue from throughput.
- sqlparse as target repo, pinned @0d24023 (B1 closed, ADR-003). Do not re-choose.
- Postgres checkpointer, 8-service compose, 3-package monorepo — decided against (descope §1/§5/§12.4).
- torch default wheel — pins to the CPU index (2.7 GB of unusable CUDA otherwise). Do not unpin.
- The grounded-RAG service is the DIRECT path, not the multi-agent graph. D5-D9 still build
  SUPERVISOR/PLANNER/EDITOR/tester/reviewer around it. Don't treat C3 as fully closed (no
  evals/test_citations_resolve.py yet) and don't tick D5's boxes because `forge ask` exists.

## Notes for the next session
- Commits OMIT the Claude co-author trailer, per the human (2026-07-23). Author is Shaimeur; do not
  re-add `Co-Authored-By: Claude` / `Claude-Session:` lines.
- Local runnable profile is `.env.local.example` → `cp .env.local.example .env`. The session hook
  blocks writing `.env` directly (transcript mirroring), so activation is the human's one command.
- One mistral fixture is recorded+committed under data/fixtures/llm/ (keyed by llm_string, so
  provider-safe). It makes the sqlparse-split query replay offline. README is no longer stale.
- Embedded Qdrant allows ONE client per path per process — build once and inject (api/main.py's
  get_resources, evals, hybrid_search all do). Run /checkpoint before you stop.
