# FORGE — working instructions

## Reading order
- **`docs/STATE.md`** — the mutable progress ledger. The session-start hook prints it into context
  automatically; read it first and trust it over your own recollection.
- **`GOALS.md`** — the full build plan: roadmap D1–D15, acceptance gates C1–C10, cut list, descope
  register, risk watchlist. Read on demand. It is not auto-loaded — it is long, and a lean STATE.md
  read every session is the cheaper design.
- **`docs/cahier-des-charges.md`** — the graded specification (French, frozen). Read on demand.
- **`docs/descope-v1.md`** — the argued deviations that actually govern scope. Read on demand.

Both spec files are the human's to edit by hand; a PreToolUse hook blocks Claude from writing them.

## What FORGE is
A multi-agent engineering assistant: it ingests a repo, plans a change, writes the patch, runs the
tests in a hardened sandbox, then shows a verified, cited diff. Solo ~14-day capstone. **One package,
`src/forge/`** — not a monorepo.

## Agent roster (cahier §4)
- **SUPERVISOR** — routing and budget only, never content
- **RETRIEVER** — all knowledge access: hybrid search, ripgrep, AST symbol lookup
- **PLANNER** — a citation-backed `ChangePlan`; a step that cites nothing is rejected
- **EDITOR** — a validated `PatchSet`, never writes to disk
- **SANDBOX_ENGINEER** — writes tests, runs everything in an isolated container
- **REVIEWER** — groundedness, plan conformance, test results, security
- **SENTINEL** — deterministic guardrail nodes wrapping the graph, not a conversational agent

## Stack — as actually built (descope governs, not the cahier)
Python 3.12 · `uv` · LangGraph 1.2 · LangChain · FastAPI + SSE · Qdrant.
- **SQLite checkpointer** (`AsyncSqliteSaver`), **not Postgres** — descope §1.
- Web UI is **Streamlit-pending-React** — descope §2 is **OPEN**; do not assume either until resolved.
- Providers: Gemini / Groq / Ollama via `init_chat_model`, three roles (router/reasoner/coder).
- Embedder: `all-MiniLM-L6-v2`, provisional on throughput; **D4's ablation decides** the final choice.
- Reranker: built for the eval harness, **off in the live path** — descope §3.

## Never cut
Sandbox hardening · the guardrail event log · the RAG ablation table · the clean-machine
`docker compose up` test. Plus the fixture/replay cache. Detail in GOALS.md.

## Session protocol
- Read the injected STATE.md before acting. Never re-plan work already under **Done**.
- Before writing code, state which roadmap day and which DoD you are working toward.
- Take the next concrete action STATE.md names; do not shop the plan.
- Commit when tests go green, message prefixed `[D<day>]`.
- Run `/checkpoint` before the session ends.

## Hard rules
- Do not edit `docs/cahier-des-charges.md` or `docs/descope-v1.md` — frozen specs, a hook blocks it.
  If one must change, record why under *Blocked / open decisions* in STATE.md and raise it.
- `docs/STATE.md` is the single source of truth for progress. Keep it under 80 lines; rewrite in
  place, never append.
- `GOALS.md`: tick boxes and update the pointer as work lands. Do not restructure the plan or rewrite
  the descope register without the human.
- Never mark a DoD done without running its command and seeing it pass. Record the command and exit
  status. If it was not run, the status is `? unverified` — an honest status, unlike a false `[x]`.
- Record blockers honestly rather than guessing and moving on.
- Never hardcode a secret, never commit `.env`, never print `.env` contents to the transcript.
- `src/forge/sandbox/` and `src/forge/guardrails/` are security-sensitive: do not relax a container
  flag, path allowlist, or resource cap without flagging it first.

## Conventions
- `uv`, never pip or poetry. `uv run <cmd>` to execute, `uv add <pkg>` to add a dependency.
- `ruff` for lint and format (line length 100). `make lint` / `make fmt`.
- Typed payloads that cross an agent boundary live in `src/forge/models.py`, not passed as dicts.
- Not done until `uv run pytest` passes. `tests/conftest.py` forces `CACHE_MODE=replay`, so tests
  never touch the network; a fixture miss is a hard error, which is also the offline demo guarantee.
