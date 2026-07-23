<!-- The mutable progress ledger. Rewritten in full at every /checkpoint, never
     appended to. Keep it under 80 lines. Nothing goes under "Done (verified)"
     unless its DoD command actually ran and passed this or a named session.
     GOALS.md is the durable plan; this file is where you are in it. -->

Last updated : 2026-07-23
Roadmap day  : D2 built (DoD not met — blocked on B1) · D3 is next
Branch       : main
Last commit  : bc1efce  Switch the default embedder on measured CPU throughput

## Done (verified)
- [x] D1 foundations — config, fixture cache, LLM factory, /v1/health, forge config/index,
      4-service compose, CPU-pinned Dockerfile, ADR-001/002 — `uv run pytest` → 68 passed (2026-07-23)
- [x] D2 AST ingestion — walker, tree-sitter Python chunker, BM25 sparse, Qdrant store (named
      dense+sparse), incremental index via git diff — `uv run pytest` → 68 passed; throughput
      measured (MiniLM 47 vs BGE-M3 1709 ms/chunk) in docs/evaluation.md
- [x] Continuity system — .claude/ hooks (session-start, protect-goals, pre-compact), STATE.md,
      /checkpoint, CLAUDE.md — hooks tested, exit codes confirmed (2026-07-23)

## In progress
- D3 retrieval. NEXT CONCRETE ACTION: choose the demo target repo — a real OSS Python project,
  3–5k LOC (descope §9), that ships a working pytest suite, no compiled extensions. Clone it at a
  pinned sha into data/target, set TARGET_REPO in .env, write it up as ADR-003. Then run
  `uv run forge index data/target` and record chunk count + wall-clock in docs/evaluation.md —
  that closes D2's DoD. Then build src/forge/rag/retrieve.py (dense + sparse over the named vectors).

## Blocked / open decisions
- B1  Target repo not chosen. data/target does not exist; D2 was measured against FORGE itself.
      Blocks D3 golden set, D4 ablation, D8 swe_mini, the whole demo. Cahier §18 action #1. UNBLOCK FIRST.
- B2  No .env, no LLM provider key verified against a live quota. Blocks every agent day (D5+).
- B3  Ollama holds mistral:latest only; config routes all roles to qwen2.5-coder:7b (~4.5 GB, not pulled).
- O1  React vs Streamlit (descope §2) — blocks D13 and the pyproject `ui` extra. Only the training's
      requirement list resolves it; descope cannot. ASK THE HUMAN.
- O2  C6 requires "Tools AND MCP" but the cut list makes MCP cuttable. Fix C6 wording before the first cut.
- D1 compose DoD (`docker compose up` → qdrant + api healthy) not re-run this session; recorded green
      in docs/evaluation.md from D1. Status here: ? unverified today.
- D2 DoD (target repo fully indexed) NOT met — see B1.

## Do not redo
- BGE-M3 as the default embedder — rejected on CPU throughput (36× slower). Kept configurable via
  EMBEDDING_MODEL; D4's ablation may buy it back on quality. Don't re-argue from throughput alone.
- Postgres checkpointer, 8-service compose, 3-package monorepo — all decided against (descope §1/§5/§12.4).
  Do not re-add them; the /remote-control bootstrap brief that proposes them is stale.
- torch default wheel — pulls 2.7 GB of CUDA that cannot run on this Intel-only machine. Pinned to the
  CPU index in pyproject. Do not unpin.

## Notes for the next session
- README is stale: claims "22 tests / Day 2", actual is 68 tests with D2 shipped. Fix on the next
  commit that touches README.md.
- The .claude/ continuity system is new as of 2026-07-23. Run /checkpoint before you stop.
