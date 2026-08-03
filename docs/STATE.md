<!-- The mutable progress ledger. Rewritten in full at every /checkpoint, never
     appended to. Keep it under 80 lines. Nothing goes under "Done (verified)"
     unless its DoD command actually ran and passed this or a named session.
     GOALS.md is the durable plan; this file is where you are in it. -->

Last updated : 2026-08-03
Roadmap day  : D1–D12 done · **D13 driven end-to-end in a real browser for the first time**;
               8 of §15.6's 9 beats seen on screen, the full agent pipeline among them
Branch       : main
Last commit  : 2870ad8 [D13] Make the needs_more_context retry actually re-retrieve

## Done (verified)
- [x] D1–D5 foundations · AST ingestion · hybrid retrieval · RAG eval (**MiniLM FROZEN**, reranker
      OFF) · LangGraph + memory · D6 Planner+Editor · D7 Sandbox · D8 Tester+repair · D9 Reviewer+HITL
      · D10 Guardrails · D11 Red team (**C5**, 32/32) · **D12 API+CLI — C8 CLOSED**
- [x] **C1 CLOSED** (02261ef) · **C3 CLOSED** (2026-07-28) · **B2 real-model proof** (2026-07-28)
- [x] Gemini 3.x content fix (94c9c3d) · Worktree bug fix (f20cf22) · Retriever in the change path
      (98f2356)
- [x] **§15.6 in the browser (2026-08-03)** — live API + `gemini-flash-latest` + docker sandbox,
      driven through the React UI with no terminal. Seen on screen, in order:
      · **index** button → 202, re-index confirmed by shifted chunk spans
      · **grounded citations** — `● grounded — 4 verified citations`, `sqlparse/lexer.py:155-161` …
      · **bug report → timeline** — all six agents narrated from `node` frames
      · **plan gate** → approved · **test author RED** (`exit=1 0 passed, 1 failed` in docker)
      · **editor → patch gate** with coloured diff → approved · **apply** · **sandbox**
        (`453 passed, 28 failed`) · **reviewer verdict revise** · **repair loop produced a
        better patch** (`isinstance(sql, str)` → `(str, bytes, TextIOBase)` + `hasattr(sql,'read')`)
      · **guardrail fired visibly** — `[REDACTED] injection.override … lexer.py:155-165`, the panel
        auto-switched to it. The model never obeyed the planted instruction.
- [x] Full suite — `CACHE_MODE=replay uv run pytest` → **366 passed, 6 skipped, exit 0**;
      `make lint` clean; `npm run build` exit 0, oxlint clean (all at 2870ad8)

## In progress
- **D13 remaining**: the **Cost panel is built and wired to `/v1/metrics` but was never seen
  rendering** — the patch modal covered it and then quota died. One click to confirm. Beat 6's
  **green** half is also unconfirmed: red→revise→better-patch is proven, the re-verify after the
  repair is not (429 mid-run). Both need ~5 spare requests, not new code.
- **Carried**: `notebooks/02_agent_traces.ipynb` (C2 — real traces now exist, incl. a full
  gate→sandbox→repair run) · `forge tools` listing 10 (C6, settle O2) · `forge review` standalone.

## Blocked / open decisions
- **O6 (new, security) — `/v1/ask` does not run the §8.2 injection scan.** `scan_chunks` is called
  in exactly one place, `core/agents/retriever.py:84`. The ask path (`rag/answer.py:answer_question`)
  has both sentinels but no scan of *retrieved* chunks, so the poisoned comment produced zero
  `injection.*` events there — it was only caught on the change path. The UI's Ask button routes
  every question down that path. **Not fixed unilaterally: guardrail wiring is the human's call.**
- **O7 (new) — retrieval cannot bridge a call hop.** SM-01's report describes `get_real_name`
  returning a trailing quote; the defect is two hops down in `utils.remove_quotes`, which is **not in
  the top 35 chunks**. It ranks 2nd once the report *names* it. The planner then asked for the wrong
  file (`missing` said "Identifier / get_real_name in sqlparse/sql.py"), so even a working retry
  cannot rescue it. Text similarity only — no call-graph expansion. Honest §12 limitation.
- **O8 (new) — ask-vs-change is decided by the UI**, not the SUPERVISOR: `Route` is
  `{RETRIEVE, ANSWER, END}` with no CHANGE member, and the session stream only ever builds the change
  graph, so a question sent down it gets *planned*. Human chose the UI toggle (2026-08-03) over
  unifying the graph, on cost grounds. Cahier §4 says the supervisor routes — expect the question.
- **Free-tier quota ≈ 20 req/day PER MODEL** — exhausted 2026-08-03 mid-repair (`429`, surfaced
  cleanly as a typed error frame, no crash). Both `gemini-flash-latest` and `gemini-3.5-flash`
  answered today, so the 503 note on 3.5-flash is stale.
- **O5** injection tier 2 (frozen → human) · **O4** reviewer/editor same family · **O2** C6 wording ·
  **B3** `qwen2.5-coder:7b` unpulled.
- **Gates: 4 of C1–C10 closed** (C1, C3, C5, C8). C7 is one Cost click + one green re-verify away.

## Do not redo
- **Worktree path**: `create_workspace` `.resolve()`s `workspace_root` (see f20cf22).
- **Gemini**: `gemini-2.5-*` 404s for post-cutover keys; `.content` is a block list.
- **Planner context** capped to top-8 (`planner.py _MAX_SNIPPETS`); `retriever_node` stays optional
  in `build_change_graph` — D9 tests inject their own pack.
- **Three bugs the browser found that no test could** (2870ad8, 04726cc): `/v1/index` opened a
  *second* embedded Qdrant client for a path this process already holds; `stream_mode="messages"`
  streamed a `with_structured_output` node's raw JSON into the chat as the assistant's reply; and the
  §5.1 re-entry re-ran `latest_user_text`, so the retry returned the identical pack. All three were
  unreachable until the UI actually called those paths.
- **Demo seeding**: `data/target` is pinned at `0d24023`; worktrees branch from **HEAD**, so a seeded
  bug must be *committed* there to reach a session. Restore with `git reset --hard 0d24023`, delete
  the `forge/*` worktrees you made, then **re-index** — the index keeps the poisoned chunk otherwise.
- `build_llm` installs a process-global cache; guardrail log never raises; SSE frames CRLF; sandbox
  exit-code is authority; embedded Qdrant: ONE client per path per process. sqlparse @0d24023.

## Notes for the next session
- Real runs: `QDRANT_URL= CACHE_MODE=auto SANDBOX_BACKEND=docker
  GEMINI_CODER_MODEL=GEMINI_REASONER_MODEL=gemini-flash-latest`. Blank `QDRANT_URL` or the embedded
  index is bypassed. Commits OMIT the Claude trailer (author Shaimeur).
- Web: `cd web && npm run dev` proxies `/v1` → :8000. API: `make api`.
- 8 new fixtures recorded today (no key material — LangChain serialises secrets by reference).
