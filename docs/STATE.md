<!-- The mutable progress ledger. Rewritten in full at every /checkpoint, never
     appended to. Keep it under 80 lines. Nothing goes under "Done (verified)"
     unless its DoD command actually ran and passed this or a named session.
     GOALS.md is the durable plan; this file is where you are in it. -->

Last updated : 2026-08-03
Roadmap day  : D1–D13 done · **D14 substantially done**. §15.6 ran in a browser (8/9 beats);
               C2 + C6-Tools landed; C9's test is written but **unrun** (no registry egress here)
Branch       : main

## Done (verified)
- [x] D1–D12 foundations · AST ingestion · hybrid retrieval · RAG eval (**MiniLM FROZEN**, reranker
      OFF) · LangGraph+memory · Planner/Editor · Sandbox · repair loop · HITL · Guardrails · Red team
      (**C5**, 32/32) · **API+CLI (C8)** · **C1** · **C3**
- [x] **§15.6 in the browser (2026-08-03)** — live API + `gemini-flash-latest` + docker sandbox:
      index → grounded citations → bug report → timeline (6 agents) → plan gate → test author RED
      (`exit=1`, docker) → patch gate + diff → apply → sandbox (`453 passed, 28 failed`) → reviewer
      `revise` → **repair loop produced a better patch** · **guardrail fired visibly**
      (`[REDACTED] injection.override … lexer.py:155-165`); the model never obeyed the planted text
- [x] **Offline replay proven** — `CACHE_MODE=replay forge ask` → exit 0, grounded, 4 citations, no
      network; identical through the UI. Cost panel: `turns 1 · calls 1 · guardrail events 3 · 6.2s`
- [x] **C2 (2026-08-03)** — `notebooks/02_agent_traces.ipynb` built from `data/checkpoints.sqlite`
      (node order recovered from `branch:to:*` writes). **All 7 code cells executed**: 3 interrupts,
      3 editor passes = 2 repair iterations, 35 guardrail events incl. the one REDACTED override.
      Form 4 (contested/escalation) is shown **wired but not fired** — the honest reading
- [x] **C6 Tools half** — `forge tools` → "FORGE tools — 10 operational". `tests/test_registry.py`
      invokes all seven knowledge tools and asserts `read_file`/`list_files` refuse a path escape
- [x] **C4 network-free** — `pytest tests/test_graph.py -k restart` → exit 0
- [x] D14 docs: `docs/architecture.md` (new) · README rewritten (was claiming "Day 7", "206 tests")
      · `requirements.txt` (383 lines) · `docs/slides.md` — 12 slides + the four jury questions
- [x] Full suite — `CACHE_MODE=replay uv run pytest` → **373 passed, 6 skipped, exit 0**;
      `make lint` clean; `npm run build` exit 0, oxlint clean

## In progress
- **C9 — the run, not the script.** `scripts/clean_machine_test.sh` exists and already found five
  defects, all fixed (below). It is **unrun**: this sandbox's Docker *daemon* has no registry egress,
  so `node:22-slim` will not pull. **Run it on a machine with egress — that is the next action.**
- **C7** — beat 6's *green* half: red→revise→better-patch is proven, the re-verify after the repair
  is not (429 mid-run). Needs ~5 spare requests. Also `docs/demo.mp4` (D15, human).
- **C10** — L1 ✓ L2 ✓ L3 ✓ L4 ✓ L6 ✓ (draft) · **L5 needs the recorded video**.

## Blocked / open decisions
- **O6 (security) — `/v1/ask` does not run the §8.2 injection scan.** `scan_chunks` is called from
  exactly one place, `core/agents/retriever.py`. The ask path has both sentinels but no scan of
  *retrieved* chunks, so the poisoned comment produced zero `injection.*` events there. The UI's Ask
  button routes every question down it. **Not fixed unilaterally — guardrail wiring is the human's.**
- **O7 — retrieval cannot bridge a call hop.** SM-01's report names `get_real_name`; the defect is two
  hops down in `utils.remove_quotes`, **absent from the top 35** (rank 2 once *named*). The planner's
  `missing` then asked for the wrong file, so no retry rescues it. Honest §12 limitation.
- **O8 — ask-vs-change is decided by the UI**, not the SUPERVISOR: `Route` has no `CHANGE` member and
  the session stream only builds the change graph. Human chose the UI toggle over unifying the graph.
- **O2 / C6 second half — MCP transport is not built** (cut-list item 1). C6 stays half closed.
- **O3** fold descope into the cahier (frozen → human) · **O5** injection tier 2 · **O4** reviewer/
  editor same family · **B3** `qwen2.5-coder:7b` unpulled.
- **Free-tier quota ≈ 20 req/day PER MODEL** — hit 2026-08-03 mid-repair (`429`, a typed error frame,
  no crash). `gemini-3.5-flash` answered today too, so the 503 note on it is stale.
- **Gates: C1 C2 C3 C5 C8 closed · C4 C6 half · C7 C9 C10 open.**

## Do not redo
- **Five defects the clean-machine test found before it even ran**: the image had no `git` (sessions
  are worktrees) and no `ripgrep` (lexical retrieval); a clone carries **no `data/target/`** (it is
  gitignored → `scripts/bootstrap_target.sh` now fetches sqlparse@0d24023 from the entrypoint);
  `.env.example` defaulted to `CACHE_MODE=auto`, silently breaking compose's "no API keys" promise;
  and **compose served no UI at all** — the Dockerfile now has a `node` stage and the API mounts
  `web/dist` at `/`, so one port serves both.
- **Five more the browser found that no test could**: `/v1/index` opened a *second* embedded Qdrant
  client; `stream_mode="messages"` streamed a structured node's raw JSON into the chat as the reply;
  the §5.1 re-entry re-ran `latest_user_text` so the retry returned the same pack; `/v1/ask` never
  recorded a turn; `per_session[].guardrail_events` was never filled.
- **Replay is only as stable as the index — re-index `--full`, never incremental, after a restore.**
  An incremental index whose HEAD moved *backwards* reordered the top-8; the prompt embeds snippets,
  so **every ask fixture missed** and `forge ask` died on `FixtureMiss` with the repo looking clean.
- `create_workspace` `.resolve()`s `workspace_root`. `gemini-2.5-*` 404s for post-cutover keys;
  `.content` is a block list. Planner context capped to top-8. `build_llm` installs a process-global
  cache. Sandbox exit-code is authority. Embedded Qdrant: ONE client per path per process — the CLI
  and the API cannot both hold it. sqlparse @0d24023.

## Notes for the next session
- Real runs: `QDRANT_URL= CACHE_MODE=auto SANDBOX_BACKEND=docker GEMINI_CODER_MODEL=
  GEMINI_REASONER_MODEL=gemini-flash-latest`. Blank `QDRANT_URL` or the embedded index is bypassed.
  `make api`; `cd web && npm run dev` proxies `/v1` → :8000. Commits OMIT the Claude trailer.
