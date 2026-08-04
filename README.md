# FORGE

**Multi-agent engineering assistant.** Ingests a codebase, plans a change, writes the patch, runs
the test suite in a hardened sandbox, and only then shows you a verified, cited diff.

> FORGE never claims a fix works — it proves it, by running the tests, and it never cites a file it
> did not actually retrieve.

*Projet de fin de formation — Systèmes Multi-Agents & RAG.*
Specification: [`docs/cahier-des-charges.md`](docs/cahier-des-charges.md) ·
Scope decisions: [`docs/descope-v1.md`](docs/descope-v1.md) ·
Architecture: [`docs/architecture.md`](docs/architecture.md)

---

## Status

Day 14 of 15. The full pipeline runs end to end in a browser: retrieval, planning, both human
approval gates, sandboxed tests, review, and the repair loop. **383 tests green**, fully offline.

| Sprint | Days | Delivers | State |
|---|---|---|---|
| 1 — Foundations & RAG | D1–D4 | Skeleton, ingestion, retrieval, RAG ablation | **done** |
| 2 — Agents | D5–D9 | LangGraph, Planner/Editor, sandbox, repair loop, HITL | **done** |
| 3 — Guardrails | D10–D11 | Sentinels, policy engine, adversarial suite | **done** (32/32) |
| 4 — Interfaces | D12–D13 | FastAPI + SSE, `forge` CLI, React UI | **done** |
| 5 — Integration | D14–D15 | Compose, benchmark, docs, defence | **in progress** |

Acceptance gates (cahier §16): **C1–C6, C8 and C9 closed.** C4 is proven network-free
(`pytest -k restart`) *and* by a container restart inside the C9 clean-machine run. C6 is closed on
both halves — the ten tools are LangChain Tools and are served over MCP (`forge mcp`,
`scripts/mcp_smoke.py`). **C7 and C10 remain open on one thing only: the recorded demo video.**
Nothing here is marked done without the command that proved it — see `docs/STATE.md`.

**What does not work yet, honestly.** Retrieval cannot follow a call graph, so a bug report that
describes a symptom two hops from the fix site will not find it — that is `docs/limitations.md` §8,
and it is why the `swe_mini` score is scoped to the repair loop rather than the whole system. The UI,
not the supervisor, decides whether a message is a question or a change request (O8). Injection
tier 2 — a classifier behind the heuristics — is not built (`limitations.md` §7).

---

## Quickstart — one command

```bash
cp .env.example .env        # defaults to CACHE_MODE=replay: no API key needed
docker compose up --build   # qdrant + api, and the React UI at the same origin
```

Then open **http://localhost:8000**. The container fetches the demo target repository
(sqlparse, pinned) on first boot and serves the built SPA itself, so there is nothing else to start.

Verify it the way the acceptance gate does:

```bash
scripts/clean_machine_test.sh    # clones HEAD into an empty dir and brings it up there (C9)
```

## Quickstart — local development

```bash
make install
cp .env.example .env
make test                        # 383 tests, fully offline
make sandbox-image               # optional: the hardened sandbox image (needs Docker)

uv run forge index data/target --full        # ingest — 617 chunks from 59 files
uv run forge search "how are SQL comments stripped"
uv run forge ask "how does sqlparse split statements?"
uv run forge tools                           # the ten callable tools (C6)
uv run forge fix "quoted identifiers keep a trailing quote"
```

The same ten tools are also served over MCP, so another agent can use FORGE's retrieval and its
sandbox. It speaks JSON-RPC on stdio, so launch it from a client's config rather than by hand —
`uv run python scripts/mcp_smoke.py` spawns it and exercises the protocol end to end:

```jsonc
// e.g. ~/.claude.json  ->  "mcpServers"
"forge": { "command": "/abs/path/to/.venv/bin/forge", "args": ["mcp"] }
```

The web UI in development runs separately and proxies `/v1` to the API:

```bash
make api                         # http://localhost:8000/docs
cd web && npm run dev            # http://localhost:5173
```

Without the sandbox image — or on a machine with no Docker socket — the sandbox degrades to a
documented `subprocess` fallback that is **not** a security boundary. Every `ExecutionReport` records
which one ran, and the gap is spelled out in [`docs/limitations.md`](docs/limitations.md) §1.

## Using it on your own project

FORGE targets **one repository per process**, set by `TARGET_REPO`. It is not switchable from the
UI — multi-repo is declared future work (cahier §12).

```bash
TARGET_REPO=/abs/path/to/your/project        # in .env
uv run forge index /abs/path/to/your/project --full
make api
```

`--full` is not optional when changing repositories: it recreates the collection. An incremental
index would leave the previous repo's chunks in place, and retrieval has no repo filter to separate
them. Your project must be a **git repo with at least one commit** (sessions are git worktrees), and
**Python** — the AST chunker handles `.py/.pyi`, plus markdown and config. The change path
additionally needs a pytest suite.

## How it stays reproducible

Every external call — **LLM completions included** — is recorded to `data/fixtures/` as readable
JSON and replayed under `CACHE_MODE=replay`, where a cache miss is a hard error and nothing touches
the network.

```bash
CACHE_MODE=auto    ...   # read through: serve from disk, else call and record
CACHE_MODE=replay  ...   # disk only. A miss raises. This is what the demo runs.
```

Those fixtures are committed on purpose: a fresh clone with no API keys reproduces the graded demo.
It covers strictly more failure modes than a local-model fallback — spent quota, provider outage, and
model drift between rehearsal and defence, not just a dead network. Secrets are stripped on write and
never take part in a cache key.

> **Re-index `--full` after restoring the target repo, then run `forge ask` in replay.** A fixture
> key includes the prompt, and the prompt embeds retrieved snippets — so an incremental re-index that
> reorders the top-8 invalidates every recorded answer at once, with the repo looking perfectly clean.

## Configuration

`.env.example` documents every setting. The ones that matter most:

| Variable | Why it matters |
|---|---|
| `CACHE_MODE` | `replay` is the offline guarantee. Anything else may hit the network. |
| `LLM_PROVIDER` | `gemini` (default) / `groq` / `ollama`. Three roles per provider — router, reasoner, coder — so the token-fat tiers are used sparingly. |
| `TARGET_REPO` | The one repository FORGE reads and patches. |
| `QDRANT_URL` | Blank uses the embedded index at `QDRANT_PATH`. Only one process may hold it at a time. |
| `RERANK_ENABLED` | Off. The cross-encoder is measured in the eval harness, not run live — no GPU on the build machine. |

## API

`GET /docs` for the full OpenAPI. Twelve routes; the ones that matter:

| Route | Purpose |
|---|---|
| `POST /v1/ask` | grounded answer with verified citations |
| `POST /v1/search` | hybrid retrieval, **no LLM** — always available |
| `POST /v1/sessions` · `/{id}/messages` | create a session; stream a run over SSE |
| `POST /v1/sessions/{id}/approve` | resume a run paused at a §5.5 gate |
| `GET /v1/guardrails/events` | the §8.5 log, queryable by session, stage and action |
| `GET /v1/metrics` | turns, LLM calls, tokens, latency, guardrail counts |

## Evaluation

RAG ablation and the `swe_mini` repair benchmark: [`docs/evaluation.md`](docs/evaluation.md).
Notebooks: `notebooks/01_rag_evaluation.ipynb`, `notebooks/02_agent_traces.ipynb`.

## Deliberate deviations from the cahier

Each is argued in [`docs/descope-v1.md`](docs/descope-v1.md) with its jury-facing justification.
The build machine has ~5 GB of usable RAM and no GPU, and one end-to-end run costs ~29 LLM calls
and ~300k input tokens.

| Cahier | Here | Because |
|---|---|---|
| §7 `AsyncPostgresSaver` | `AsyncSqliteSaver` | Postgres buys multi-writer concurrency, which §3.2 puts out of scope. C4 passes identically. |
| §12.3 eight services | four (two by default) | C9's test is a clean machine — the examiner's laptop. Two that start beat eight that OOM; `ollama` and `sandbox-image` sit behind profiles. |
| §6.5 live reranker | eval harness only | No GPU. The ablation table reports what that costs. |
| §13.3 ten `swe_mini` bugs | four | Quota. Four with honest numbers beats ten extrapolated. |
| §6.1 `docs` corpus | cut | A second ingestion pipeline; the injection demo uses a poisoned repo comment. |
| §9 tools via MCP | **built** — both surfaces | Not descoped after all. §16's C6 says "Tools *and* MCP" while the cut list called MCP droppable because the requirement says "or" (O2). Building it settles the contradiction instead of arguing it. |

§10.1's React requirement was **kept** (O1, resolved D13): `web/` is a Vite + TypeScript + Tailwind
build, served by the API in the container.

## Layout

```
src/forge/
├── config.py        settings, model routing, budget + sandbox caps
├── cache/           record/replay fixture store
├── llm/             provider-agnostic chat model factory
├── core/            LangGraph state, the six agents, workspaces
├── rag/             ingest, AST chunking, embed, retrieve, answer
├── sandbox/         hardened ephemeral executor
├── guardrails/      sentinel_in / injection / policy / sentinel_out
├── tools/           the ten callable tools + registry
├── mcp/             the same ten over MCP — reflected, not reimplemented
├── api/             FastAPI: §11 routes, SSE, session store
└── cli/             the `forge` command
web/                 React + Vite + Tailwind SPA (separate build)
```

The cahier's §12.4 splits this across three installable packages. It is one package here, with the
same conceptual boundaries — a solo 14-day build does not repay monorepo overhead.

## Licence

Coursework. No licence granted.
