<!-- The mutable progress ledger. Rewritten in full at every /checkpoint, never
     appended to. Keep it under 80 lines. Nothing goes under "Done (verified)"
     unless its DoD command actually ran and passed this or a named session.
     GOALS.md is the durable plan; this file is where you are in it. -->

Last updated : 2026-08-04
Roadmap day  : **D1–D14 done — D14 DoD MET (both halves)**. §15.6 ran in a browser (8/9 beats);
               **C9 PASSED and swe_mini ran 4/4 today**. D15 left: C7/C10 need the video
Branch       : main

## Done (verified)
- [x] D1–D12 foundations · AST ingestion · hybrid retrieval · RAG eval (**MiniLM FROZEN**, reranker
      OFF) · LangGraph+memory · Planner/Editor · Sandbox · repair loop · HITL · Guardrails · Red team
      (**C5**, 32/32) · **API+CLI (C8)** · **C1** · **C3**
- [x] **C9 (2026-08-04)** — `API_PORT=18000 scripts/clean_machine_test.sh` → **exit 0, "C9 PASS"**.
      Depth-1 clone of `84d1a9f`, no `.env`/index/`node_modules`: `docker compose up --build` alone
      gave `{"cache_mode":"replay","offline":true}`, openapi, **the SPA on the same origin**, an index
      built *inside* the container, a session alive across `restart api`. Fuller record in GOALS.md
- [x] **C4 both halves** — `pytest tests/test_graph.py -k restart` → exit 0, **and** the container
      restart inside the C9 run above
- [x] **D14 DoD MET — the benchmark (2026-08-04)**. `--verify` → exit 0, 4 bugs sound (no model); the
      real run → **exit 0, 4/4 REPAIRED, 0 regressions, 1.0 iterations, 80 s, 11 calls, 20 613
      tokens**, and it **replays offline in 25 s**. `docs/evaluation.md` carries the three caveats:
      **retrieval is bypassed** (scores the repair loop, not the system), 1.0 iterations = **the loop
      never iterated**, A5 check passed on a name-string only. O7 → `limitations.md` §8
- [x] **§15.6 in the browser (2026-08-03)** — live API + `gemini-flash-latest` + docker sandbox:
      index → citations → bug report → timeline (6 agents) → plan gate → test author RED (`exit=1`)
      → patch gate + diff → apply → sandbox (`453 passed, 28 failed`) → reviewer `revise` → **the
      repair loop produced a better patch** · **guardrail fired visibly** (`[REDACTED]
      injection.override`); the model never obeyed the planted text
- [x] **Offline replay proven** — `CACHE_MODE=replay forge ask` → exit 0, grounded, 4 citations, no
      network; identical through the UI. Cost panel: `turns 1 · calls 1 · guardrail events 3 · 6.2s`
- [x] **C2 (2026-08-03)** — `notebooks/02_agent_traces.ipynb` from `data/checkpoints.sqlite`, **all 7
      cells run**: 3 interrupts, 2 repair iterations, 35 guardrail events; Form 4 wired but not fired
- [x] **C6 Tools half** — `forge tools` → "FORGE tools — 10 operational". `tests/test_registry.py`
      invokes all seven knowledge tools and asserts `read_file`/`list_files` refuse a path escape
- [x] D14 docs: `architecture.md` (new) · README rewritten · `requirements.txt` (383) ·
      `docs/slides.md` — 12 slides + the four jury questions in an annexe
- [x] Full suite — `CACHE_MODE=replay uv run pytest` → **373 passed, 6 skipped, exit 0**;
      `make lint` clean; `npm run build` exit 0, oxlint clean

## In progress
- **C7** — beat 6's *green* half: red→revise→better-patch is proven, the re-verify after the repair
  is not (429 mid-run). Needs ~5 spare requests. Also `docs/demo.mp4` (D15, human). **Next action.**
- **C10** — L1 ✓ L2 ✓ L3 ✓ L4 ✓ L6 ✓ (draft) · **L5 needs the recorded video**.

## Blocked / open decisions
- **O6 (security) — `/v1/ask` does not run the §8.2 injection scan.** `scan_chunks` is called only
  from `core/agents/retriever.py`; the ask path has both sentinels but never scans *retrieved* chunks,
  and the UI's Ask button routes every question down it. **Guardrail wiring is the human's call.**
- **O7 — retrieval cannot bridge a call hop.** Now written up as **`limitations.md` §8** with the fix
  scoped: ingestion records definition boundaries and **no call edges**, so the one-hop expansion
  must extract callees and resolve them through `tools/ast_symbols.py`. Not started.
- **O8 — ask-vs-change is decided by the UI**, not the SUPERVISOR: `Route` has no `CHANGE` member and
  the session stream only builds the change graph. Human chose the UI toggle over unifying the graph.
- **O2 / C6 second half — MCP transport not built** (cut-list 1) · **B3** `qwen2.5-coder:7b` unpulled.
- **O3** descope → cahier (human) · **O5** injection tier 2 · **O4** reviewer/editor same family.
- **Quota: ≈20 free req/day PER MODEL** — hit 2026-08-03 mid-repair (`429`, typed frame, no crash).
- **Gates: C1 C2 C3 C4 C5 C8 C9 closed · C6 half · C7 C10 open (both want the video).**

## Do not redo
- **Ten defects already found and fixed** — five by the clean-machine test before it ever ran (no
  `git`/`ripgrep` in the image; a clone has no `data/target/` → the entrypoint runs
  `bootstrap_target.sh`; `.env.example` defaulted to `CACHE_MODE=auto`; compose served **no UI**),
  five by the browser that no test could (`/v1/index` opened a *second* embedded Qdrant client;
  `stream_mode="messages"` streamed a node's raw JSON as the reply; §5.1 re-entry re-ran
  `latest_user_text`; `/v1/ask` never recorded a turn). Full detail in GOALS.md D13/D14.
- **Replay is only as stable as the index — re-index `--full`, never incremental, after a restore.**
  An incremental index whose HEAD moved *backwards* reordered the top-8; the prompt embeds snippets,
  so **every ask fixture missed** and `forge ask` died on `FixtureMiss` with the repo looking clean.
- `create_workspace` `.resolve()`s `workspace_root`. `gemini-2.5-*` 404s for post-cutover keys;
  `.content` is a block list. Planner context capped to top-8. `build_llm` installs a process-global
  cache. Sandbox exit-code is authority. sqlparse @0d24023. Embedded Qdrant: ONE client per path per
  process (CLI and API cannot both hold it), and a run from a subdirectory drops an *empty* index
  there — root-anchored ignores missed `web/data/qdrant/`, so `**/data/qdrant/` now covers it.

## Notes for the next session
- Real runs (worked 2026-08-04): `QDRANT_URL= CACHE_MODE=auto SANDBOX_BACKEND=docker
  GEMINI_REASONER_MODEL=gemini-flash-latest` — coder keeps `gemini-3.5-flash`; two IDs = two quota
  pools. `make api`; `cd web && npm run dev` proxies `/v1` → :8000. Commits OMIT the Claude trailer.
