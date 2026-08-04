<!-- The mutable progress ledger. Rewritten in full at every /checkpoint, never
     appended to. Keep it under 80 lines. Nothing goes under "Done (verified)"
     unless its DoD command actually ran and passed this or a named session.
     GOALS.md is the durable plan; this file is where you are in it. -->

Last updated : 2026-08-04
Roadmap day  : **D1–D15 done, plus D15b (UI repo control).** Eight gates closed;
               **C7 and C10 wait on one thing: `docs/demo.mp4`.** Defence at 11:00.
Branch       : main

## Done (verified)
- [x] D1–D14 foundations · AST ingestion · hybrid retrieval · RAG eval (**MiniLM FROZEN**, reranker
      OFF) · LangGraph+memory · Planner/Editor · Sandbox · repair loop · HITL · Guardrails ·
      Red team (**C5**, 32/32) · API+CLI (**C8**) · **C1 C2 C3 C4 C9** · swe_mini 4/4
- [x] **C6 CLOSED, both halves (D15)** — `forge tools` → 10 operational; `src/forge/mcp/` *reflects*
      `build_toolset()` (so path confinement is inherited, not rebuilt); `scripts/mcp_smoke.py`
      spawns the real console script and speaks JSON-RPC → **exit 0, "C6/MCP PASS"**. **O2 settled**
- [x] **O6 CLOSED (D15)** — `/v1/ask` and `forge ask` now run the §8.2 scan, inside
      `answer_question` where retrieved text becomes prompt. Proven by stubbing the scan out and
      watching the test fail. A clean chunk returns as the *same object*, so no fixture moved
- [x] **O7 built and measured (D15)**, shipped **off**. SM-01: pulls `remove_quotes` into the pack
      for ~390 tokens. Golden set: **zero movement on every metric**, +11.8 % tokens — its 42
      questions all name the symbol they want. Tables in `limitations.md` §8 + `evaluation.md`
- [x] **The offline demo was dead on a fresh clone, and is fixed (D15)** — see *Do not redo*
- [x] **D15b — UI repo control, a deliberate un-freeze.** Tier 1: `Rebuild index` does a full
      rebuild (it silently did nothing before). Tier 2: `GET /v1/repos` + `POST /v1/target` + a
      `<select>` — **the browser selects, it never supplies**, because `target_repo` is the file
      tools' confinement root. Tier 3 (evals in the UI) argued against, not taken. Two holes found
      by *running* it rather than reasoning about it — both in *Do not redo*
- [x] Verified after D15b, every one exit 0: `CACHE_MODE=replay uv run pytest` → **415 passed,
      6 skipped** · `evals/security` → **32/32 mitigated** · `evals/test_citations_resolve.py` ·
      `scripts/clean_machine_test.sh` (**C9 PASS**, twice) · `sse_smoke.sh` · `mcp_smoke.py` ·
      `stage_demo.sh warm` · `npm run build` + oxlint · `make lint` ·
      **`run_swe_mini.py` in replay → 4/4 repaired, offline, 25 s, zero quota** (a spare demo beat)
- [x] **§15.6 in the browser (2026-08-03)** — index → citations → bug report → timeline (6 agents) →
      plan gate → test author RED → patch gate + diff → apply → sandbox → reviewer `revise` →
      **a better patch** · **the guardrail fired visibly**. 8 of 9 beats; the 9th died on a 429

## In progress — everything left is human
- **C7 and C10 — the video, and nothing else.** The §15.6 run is already proven in a browser; what
  is missing is the *recording*. Follow **`docs/run-sheet.md`**: every beat, the question wordings
  that have fixtures, a recovery column, and the D15b picker as an optional 7th beat. **Next action.**
- Then: three stopwatch rehearsals · say the eight jury answers out loud (`slides.md`, two annexes) ·
  re-run `clean_machine_test.sh` in the morning.
- `d15-freeze` tags the pre-D15b tree. D15b is committed after it, deliberately.

## Blocked / open decisions
- **Your `.env` still pins `GEMINI_REASONER_MODEL=gemini-3.5-flash`** (a hook blocks me from editing
  it). Change that line to **`gemini-flash-latest`** or every offline `forge ask` misses;
  `stage_demo.sh warm` detects it and prints the fix. `config.py`/`.env.example` are corrected.
- **O8 — ask-vs-change is decided by the UI**, not the SUPERVISOR: `Route` has no `CHANGE` member.
- **O5** injection tier 2 not built · **O3** descope → cahier (frozen, human) · **O4** same family ·
  **B3** `qwen2.5-coder:7b` unpulled. **A second golden set** in the SM-01 shape (fix site unnamed)
  is the honest next retrieval work — O7 cannot be measured by the golden set that exists.
- **Quota: ≈20 free req/day PER MODEL.** Two model ids = two pools; keep the coder on a second id.
- **Gates: C1–C6, C8, C9 closed · C7 C10 open, both on the video alone.**

## Do not redo
- **The fixture cache key contains the model id.** All 37 grounded-answer fixtures were recorded
  under `gemini-flash-latest`; shipping `gemini-3.5-flash` made every recorded answer unreachable and
  replay raise FixtureMiss **with the repo looking perfectly clean**. A green suite and the C9 run
  both missed it — neither drives a real completion. `tests/test_llm_cache.py` now asserts it.
- **Re-index `--full`, never incremental, after a restore.** A full rebuild at the pinned sha
  reproduces the ordering and replay survives; an incremental one after HEAD moved does not.
- **A question's exact wording is part of the cache key.** Only recorded questions replay offline.
- **`walker.SKIP_DIRS` prunes *inside* a repo; it does not judge candidate repos.** It lists build
  output (`build`, `dist`, **`target`**), so reusing it hid `data/target` from the picker — switching
  away worked, switching back was refused. `api/repos._NEVER_A_REPO` is the narrow list.
- **Enumerating a directory is not containment.** A symlink inside a root resolved outside it and was
  offered as selectable. `_contained()` compares realpaths against the roots.
- **Ten earlier defects** — five from the clean-machine test, five from the browser (GOALS D13/D14).
  `create_workspace` `.resolve()`s `workspace_root`. `gemini-2.5-*` 404s for post-cutover keys;
  `.content` is a block list. Planner context capped to top-8. `build_llm` installs a process-global
  cache. Sandbox exit-code is authority. sqlparse @0d24023. Embedded Qdrant: ONE client per path per
  process (`QDRANT_URL=` to use it). `api.main` caches `settings`; `reset_resources()` drops it.

## Notes for the next session
- Real runs: `QDRANT_URL= CACHE_MODE=auto SANDBOX_BACKEND=docker`. `make api`;
  `cd web && npm run dev` proxies `/v1` → :8000. Commits OMIT the Claude trailer.
- `data/demo-notes/` is a throwaway second repo so the picker has two options; gitignored, `rm -rf`
  to remove. 15 stale worktrees under `data/workspaces/`; `git -C data/target worktree prune` tidies.
