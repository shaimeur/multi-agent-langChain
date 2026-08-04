<!-- The mutable progress ledger. Rewritten in full at every /checkpoint, never
     appended to. Keep it under 80 lines. Nothing goes under "Done (verified)"
     unless its DoD command actually ran and passed this or a named session.
     GOALS.md is the durable plan; this file is where you are in it. -->

Last updated : 2026-08-04
Roadmap day  : **D1–D15 code work done.** Eight gates closed; **C7 and C10 wait on one thing:
               `docs/demo.mp4`.** The tree is verified and ready to freeze.
Branch       : main

## Done (verified)
- [x] D1–D14 foundations · AST ingestion · hybrid retrieval · RAG eval (**MiniLM FROZEN**, reranker
      OFF) · LangGraph+memory · Planner/Editor · Sandbox · repair loop · HITL · Guardrails ·
      Red team (**C5**, 32/32) · API+CLI (**C8**) · **C1 C2 C3 C4 C9** · swe_mini 4/4
- [x] **C6 CLOSED, both halves (D15)** — Tools: `forge tools` → 10 operational. MCP: `src/forge/mcp/`
      *reflects* `build_toolset()` (no reimplementation, so path confinement is inherited rather than
      rebuilt); `forge mcp` on stdio; `uv run python scripts/mcp_smoke.py` spawns the real console
      script and speaks JSON-RPC to it → **exit 0, "C6/MCP PASS"**. **O2 settled by building it**
- [x] **O6 CLOSED (D15)** — `/v1/ask` and `forge ask` now run the §8.2 scan. It sits inside
      `answer_question`, the seam where retrieved text becomes prompt. Proven by stubbing the scan
      out and watching the test fail on the injection reaching the prompt. A clean chunk comes back
      as the *same object*, so no fixture moved
- [x] **O7 built and measured (D15)** — `rag/callgraph.py` + `pack_context(expand_calls=…)`, shipped
      **off**. SM-01: pulls `remove_quotes` into the pack for ~390 tokens. Golden set: **zero
      movement on every metric**, +11.8 % tokens — its 42 questions all name the symbol they want, so
      there is no hop to bridge. Tables in `limitations.md` §8 and `evaluation.md`
- [x] **The offline demo was dead on a fresh clone, and is fixed (D15)** — see *Do not redo*
- [x] Verified on the final tree, every one exit 0: `CACHE_MODE=replay uv run pytest` → **396 passed,
      6 skipped** · `evals/security` → **32/32 mitigated** · `evals/test_citations_resolve.py` ·
      `scripts/clean_machine_test.sh` (**C9 PASS**, with the new `mcp` dep in the image) ·
      `scripts/sse_smoke.sh` · `scripts/mcp_smoke.py` · `scripts/stage_demo.sh warm` ·
      `npm run build` · `make lint`
- [x] **§15.6 in the browser (2026-08-03)** — live API + real model + docker sandbox: index →
      citations → bug report → timeline (6 agents) → plan gate → test author RED → patch gate + diff
      → apply → sandbox → reviewer `revise` → **the repair loop produced a better patch** ·
      **the guardrail fired visibly**. 8 of 9 beats; the 9th died on a quota 429, not on a defect

## In progress
- **C7 and C10 — the video, and nothing else.** The §15.6 run is already proven in a browser; what
  is missing is the *recording*. `scripts/stage_demo.sh warm`, then `plant` at the security beat.
  Human-only. **Next action.**
- Then: three stopwatch rehearsals · say the four jury answers out loud · re-run
  `clean_machine_test.sh` on the morning of D15 · freeze at noon.

## Blocked / open decisions
- **Your `.env` still pins `GEMINI_REASONER_MODEL=gemini-3.5-flash`** (a hook blocks me from editing
  it). Change that line to **`gemini-flash-latest`** or every offline `forge ask` misses;
  `stage_demo.sh warm` detects it and prints the fix. `config.py`/`.env.example` are corrected.
- **O8 — ask-vs-change is decided by the UI**, not the SUPERVISOR: `Route` has no `CHANGE` member.
  Human chose the UI toggle over unifying the graph. Unchanged.
- **O5** injection tier 2 not built (`limitations.md` §7) · **O3** descope → cahier (frozen, human) ·
  **O4** reviewer/editor same family · **B3** `qwen2.5-coder:7b` unpulled.
- **A second golden set is the honest next retrieval work** — O7 cannot be measured by the one that
  exists. It needs questions in the SM-01 shape, where the fix site is deliberately unnamed.
- **Quota: ≈20 free req/day PER MODEL.** Two model ids = two pools; keep the coder on a second id.
- **Gates: C1–C6, C8, C9 closed · C7 C10 open, both on the video alone.**

## Do not redo
- **The fixture cache key contains the model id.** All 37 grounded-answer fixtures were recorded
  under `gemini-flash-latest`; shipping `gemini-3.5-flash` made every recorded answer unreachable and
  `CACHE_MODE=replay forge ask` raise FixtureMiss **with the repo looking perfectly clean**. A green
  suite and the C9 run both missed it — neither drives a real completion. `tests/test_llm_cache.py`
  now asserts the agreement. **Never rename a model without re-recording.**
- **Re-index `--full`, never incremental, after a restore.** Re-verified on D15: a full rebuild at
  the pinned sha reproduces the ordering and replay survives. An incremental one after HEAD moved
  does not — the prompt embeds snippets, so every ask fixture misses at once.
- **A question's exact wording is part of the cache key.** Only recorded questions replay offline;
  the README now quotes one that does.
- **Ten earlier defects** — five found by the clean-machine test, five by the browser. GOALS.md D13/D14.
- `create_workspace` `.resolve()`s `workspace_root`. `gemini-2.5-*` 404s for post-cutover keys;
  `.content` is a block list. Planner context capped to top-8. `build_llm` installs a process-global
  cache. Sandbox exit-code is authority. sqlparse @0d24023. Embedded Qdrant: ONE client per path per
  process (pass `QDRANT_URL=` to use it — the `.env` URL points at a server that is not running).
  `api.main` caches `settings` beside the client; `reset_resources()` exists for tests that move it.

## Notes for the next session
- Real runs: `QDRANT_URL= CACHE_MODE=auto SANDBOX_BACKEND=docker`. `make api`;
  `cd web && npm run dev` proxies `/v1` → :8000. Commits OMIT the Claude trailer.
- 15 stale worktrees under `data/workspaces/` from past runs. Harmless; remove them and
  `git -C data/target worktree prune` if you want a tidy tree on camera.
