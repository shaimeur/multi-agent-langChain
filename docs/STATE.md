<!-- The mutable progress ledger. Rewritten in full at every /checkpoint, never
     appended to. Keep it under 80 lines. Nothing goes under "Done (verified)"
     unless its DoD command actually ran and passed this or a named session.
     GOALS.md is the durable plan; this file is where you are in it. -->

Last updated : 2026-07-28
Roadmap day  : D1–D12 done · **B2 largely resolved** (real model on RAG + repair loop) · D13
               (React Web UI, O1 resolved) is next
Branch       : main
Last commit  : ccfe8b1 [evals] Record repair-loop fixtures — full swe_mini real run (3/4)

## Done (verified)
- [x] D1–D5 — foundations · AST ingestion (617 chunks) · hybrid retrieval · RAG eval
      (**MiniLM FROZEN**, reranker OFF) · LangGraph + memory (restart proven offline)
- [x] D6 Planner+Editor · D7 Sandbox (15 flags via `docker inspect`) · D8 Tester+repair loop ·
      D9 Reviewer+HITL (both `interrupt()` gates) · D10 Guardrails · D11 Red team (**C5**, 32/32)
- [x] **D12 API+CLI — C8 CLOSED**. All §11 routes, SSE (CRLF), `forge fix` with both gates
- [x] **C1 CLOSED** (02261ef) — `pytest tests/test_agents.py -k distinct` → exit 0; 6 distinct agents
- [x] **C3 CLOSED** (2026-07-28) — full RAG pipeline: `forge ask` grounded + `file:line` citations
      (live + replay); `uv run pytest evals/test_citations_resolve.py` → exit 0
- [x] **B2 real-model proof (2026-07-28)** — Gemini key verified against live quota. A real model
      (`gemini-flash-latest`) drives EDITOR/TESTER/REVIEWER + RAG, live and offline-replayable:
      · `forge ask` → **● grounded**, `file:line` citations resolve (`sqlparse/__init__.py:68-80`)
      · `swe_mini` for real — `--verify` 4/4 sound (docker); repair **3/4** (SM-03 hit budget);
        both reproduce under `CACHE_MODE=replay` from committed fixtures (ec964c7, ccfe8b1)
- [x] **Gemini 3.x content fix** (94c9c3d) — `.content` is a block list now; `llm/output.py`
      `content_to_text` flattens it; `with_structured_output` agents were already immune
- [x] **Worktree bug fix** (f20cf22) — relative `WORKSPACE_ROOT` lost the worktree (see Do not redo)
- [x] Full suite — `CACHE_MODE=replay uv run pytest` → exit 0 (359 passed, 6 skipped); ruff clean

## In progress
- **D13 Web UI — frontend built** (125c5db) — `web/` React+Vite+TS+Tailwind: sessions sidebar,
  SSE-streamed chat, agent timeline, plan/patch approval modals, diff + tests panels. `npm run build`
  (`tsc -b && vite build`) → exit 0, oxlint clean. `web/` is a separate build; dev proxies `/v1` → :8000.
  Remaining: citations panel + the in-browser §15.6 run (**C7**) — needs a live API + real model (quota).
- **Full graph reached the editor on a real model** (2026-07-29) — fixed the planner drowning in the
  ~40-chunk pack (cap 8, `planner.py`); a real run went planner → both gates (auto-approved) →
  regression → editor before the daily quota hit. Back half (editor/tester/reviewer) proven by swe_mini.
  Remaining: one contiguous green run + trace capture (C2/C7) once quota resets. Driver in scratchpad.
- **Carried**: `notebooks/02_agent_traces.ipynb` (C2 — now unblocked, real traces exist) · `forge tools`
  listing 10 (C6, settle O2 wording) · `forge review` standalone.

## Blocked / open decisions
- **Canonical coder/reasoner model** — `gemini-3.5-flash` (config default) is **503-throttled** on the
  free tier; live runs used `gemini-flash-latest`. Fixture keys are model-dependent, so replay needs
  `GEMINI_CODER_MODEL=GEMINI_REASONER_MODEL=gemini-flash-latest`, or re-record under the pinned model.
- **.env**: `QDRANT_URL=http://localhost:6333` breaks offline `forge ask` (no server) — blank it for the
  embedded index, or `docker compose up qdrant`. (`WORKSPACE_ROOT` relative is now handled in code.)
- **Free-tier daily quota ≈ 20 requests/day PER MODEL** (gemini-3.6-flash, via `flash-latest`, exhausted
  2026-07-29). Real runs are severely rate-limited → record fixtures early and demo in replay.
- **O5** injection tier 2 (descope entry needed, frozen → human) · **O4** reviewer/editor same family,
  one provider · **O2** C6 "Tools AND MCP" wording · **O3** `make lint` red pre-D7 (`make fmt` fixes) ·
  **B3** `qwen2.5-coder:7b` unpulled.
- **Gates: 4 of C1–C10 closed (C1, C3, C5, C8)** at D12 of D15. C2 needs the notebook; the planner /
  full `forge fix` real-model run feeds C2 + C7.

## Do not redo
- **Worktree path**: `create_workspace` now `.resolve()`s `workspace_root`. A *relative* WORKSPACE_ROOT
  made `git worktree add` (cwd=repo) build the tree under `data/target/…` while `Workspace.path` resolved
  against the process cwd — the sandbox then mounted an empty dir (import errors, seed misses). This, not
  drift, is why swe_mini read "unsound". Real swe_mini runs need `SANDBOX_BACKEND=docker`.
- **Gemini models**: `gemini-2.5-*` **404 for keys created after Google's mid-2026 cutover**; use the 3.5
  tier / `-latest` aliases. New keys look like `AQ.Ab…`, not `AIza…`. `.content` is a block list (above).
- **Planner context**: capped to top-8 (`planner.py _MAX_SNIPPETS`) — the full ~40-chunk pack makes a
  thinking model return an empty `ChangePlan`. **`forge fix` wires no retriever**, so its planner starts
  on an empty pack and dead-ends at END; wire `retriever_node` into its `build_change_graph` call.
- **`build_llm` installs a process-global LangChain cache**; `reset_llm_cache()` + autouse conftest undo
  it. Guardrail log never raises. SSE frames CRLF. Sandbox: `RLIMIT_DATA`, exit-code is authority, image
  is dep-free (pytest/ruff only), `/work` = the bind-mounted worktree. sqlparse @0d24023.

## Notes for the next session
- Real runs: `CACHE_MODE=auto SANDBOX_BACKEND=docker GEMINI_CODER_MODEL=gemini-flash-latest
  GEMINI_REASONER_MODEL=gemini-flash-latest`. Commits OMIT the Claude trailer (author Shaimeur).
- `sandbox/` + `guardrails/` security-sensitive: flag any flag/cap/allowlist change. `make sandbox-image`
  builds `forge-sandbox:latest`. Embedded Qdrant: ONE client per path per process.
