<!-- The mutable progress ledger. Rewritten in full at every /checkpoint, never
     appended to. Keep it under 80 lines. Nothing goes under "Done (verified)"
     unless its DoD command actually ran and passed this or a named session.
     GOALS.md is the durable plan; this file is where you are in it. -->

Last updated : 2026-08-03
Roadmap day  : D1–D12 done · **D13 driven end-to-end in a real browser**; 8 of §15.6's 9 beats seen
               on screen (live), and the ask path re-verified **fully offline** in replay
Branch       : main

## Done (verified)
- [x] D1–D5 foundations · AST ingestion · hybrid retrieval · RAG eval (**MiniLM FROZEN**, reranker
      OFF) · LangGraph + memory · D6 Planner+Editor · D7 Sandbox · D8 Tester+repair · D9 Reviewer+HITL
      · D10 Guardrails · D11 Red team (**C5**, 32/32) · **D12 API+CLI — C8 CLOSED**
- [x] **C1 CLOSED** (02261ef) · **C3 CLOSED** (2026-07-28) · **B2 real-model proof** (2026-07-28) ·
      Gemini 3.x content fix (94c9c3d) · worktree fix (f20cf22) · retriever in change path (98f2356)
- [x] **§15.6 in the browser (2026-08-03)** — live API + `gemini-flash-latest` + docker sandbox,
      through the React UI, no terminal. Seen on screen, in order:
      · **index** → 202 · **grounded citations** (`● grounded — 4 verified`) · **bug report →
        timeline**, all six agents · **plan gate** → approved · **test author RED** (`exit=1`,
        docker) · **patch gate** + coloured diff → approved · **apply** · **sandbox** (`453 passed,
        28 failed`) · **reviewer: revise** · **repair loop produced a better patch**
        (`isinstance(sql, str)` → `(str, bytes, TextIOBase)` + `hasattr(sql,'read')`)
      · **guardrail fired visibly** — `[REDACTED] injection.override … lexer.py:155-165`, panel
        auto-switched. The model never obeyed the planted instruction.
- [x] **Offline replay proven (2026-08-03)** — `CACHE_MODE=replay forge ask` → exit 0, grounded, 4
      citations, no network; same question through the UI identical. Cost panel: `turns 1 · calls 1 ·
      guardrail events 3 · 6.2s`.
- [x] Full suite — `CACHE_MODE=replay uv run pytest` → **367 passed, 6 skipped, exit 0**;
      `make lint` clean; `npm run build` exit 0, oxlint clean

## In progress
- **D13 remaining**: only beat 6's **green** half — red→revise→better-patch is proven, the re-verify
  after the repair is not (429 mid-run). ~5 spare requests, not new code. Cost panel's **`tokens`
  still reads 0**: usage metadata is never plumbed out of `ground_answer` into `record_turn`.
- **Carried**: `notebooks/02_agent_traces.ipynb` (C2 — real traces exist now, incl. a full
  gate→sandbox→repair run) · `forge tools` 10 (C6, O2) · `forge review` standalone.

## Blocked / open decisions
- **O6 (new, security) — `/v1/ask` does not run the §8.2 injection scan.** `scan_chunks` is called in
  exactly one place, `core/agents/retriever.py:84`. The ask path has both sentinels but no scan of
  *retrieved* chunks, so the poisoned comment produced zero `injection.*` events there — only the
  change path caught it. The UI's Ask button routes every question down it. **Not fixed
  unilaterally: guardrail wiring is the human's call.**
- **O7 (new) — retrieval cannot bridge a call hop.** SM-01's report names `get_real_name`; the defect
  is two hops down in `utils.remove_quotes`, **absent from the top 35** (rank 2 once *named*). The
  planner's `missing` then asked for the wrong file, so no retry rescues it. Honest §12 limitation.
- **O8 (new) — ask-vs-change is decided by the UI**, not the SUPERVISOR: `Route` is `{RETRIEVE,
  ANSWER, END}` with no CHANGE member and the session stream only builds the change graph, so a
  question sent down it gets *planned*. Human chose the UI toggle (2026-08-03) over unifying the
  graph, on cost grounds. Cahier §4 says the supervisor routes — expect the question.
- **Free-tier quota ≈ 20 req/day PER MODEL** — hit 2026-08-03 mid-repair (`429`, a typed error frame,
  no crash). `gemini-3.5-flash` answered today too, so the 503 note on it is stale.
- **O5** injection tier 2 (frozen → human) · **O4** reviewer/editor same family · **O2** C6 wording ·
  **B3** `qwen2.5-coder:7b` unpulled.
- **Gates: 4 of C1–C10 closed** (C1, C3, C5, C8). C7 is one green re-verify away.

## Do not redo
- `create_workspace` `.resolve()`s `workspace_root` (f20cf22). `gemini-2.5-*` 404s for post-cutover
  keys; `.content` is a block list. Planner context capped to top-8 (`_MAX_SNIPPETS`);
  `retriever_node` stays optional in `build_change_graph` (D9 tests inject their own pack).
- **Five bugs the browser found that no test could**: `/v1/index` opened a *second* embedded Qdrant
  client for a path this process holds; `stream_mode="messages"` streamed a structured node's raw JSON
  into the chat as the reply; the §5.1 re-entry re-ran `latest_user_text` so the retry returned the
  same pack; `/v1/ask` never recorded a turn; `per_session[].guardrail_events` was never filled.
- **Demo seeding**: worktrees branch from **HEAD**, so a seeded bug must be *committed* in
  `data/target`. Restore: `git reset --hard 0d24023`, drop your `forge/*` worktrees, re-index.
- **Replay is only as stable as the index — re-index FULL, never incremental, after a restore.** An
  `--incremental` index whose HEAD moved *backwards* leaves ranking subtly wrong: `lexer.py:23-152`
  fell out of the top-8. The prompt embeds the snippets, so **every ask fixture missed** and `forge
  ask` died on `FixtureMiss` — the offline guarantee gone, repo looking clean. `forge index
  data/target --full` (617 chunks/59 files/16s) restored the recorded pack exactly.
  **Before the defence: full re-index, then `forge ask` in replay.**
- `build_llm` installs a process-global cache; guardrail log never raises; SSE frames CRLF; sandbox
  exit-code is authority; embedded Qdrant: ONE client per path per process. sqlparse @0d24023.

## Notes for the next session
- Real runs: `QDRANT_URL= CACHE_MODE=auto SANDBOX_BACKEND=docker GEMINI_CODER_MODEL=
  GEMINI_REASONER_MODEL=gemini-flash-latest` — blank `QDRANT_URL` or the embedded index is bypassed.
  `cd web && npm run dev` proxies `/v1` → :8000; API via `make api`. Commits OMIT the Claude trailer.
