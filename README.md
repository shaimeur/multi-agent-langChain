# FORGE

**Multi-agent engineering assistant.** Ingests a codebase, plans a change, writes the patch, runs
the test suite in a hardened sandbox, and only then shows you a verified, cited diff.

> FORGE never claims a fix works — it proves it, by running the tests, and it never cites a file it
> did not actually retrieve.

*Projet de fin de formation — Systèmes Multi-Agents & RAG.*
Specification: [`docs/cahier-des-charges.md`](docs/cahier-des-charges.md) ·
Scope decisions: [`docs/descope-v1.md`](docs/descope-v1.md)

---

## Status

Day 7 of 15. Ingestion, hybrid retrieval, a **runnable grounded-RAG service** (`forge ask` /
`POST /v1/ask`), the checkpointed LangGraph, the Planner/Editor patch path and the **hardened
execution sandbox** work today. The repair loop, guardrails and web UI land over the coming sprints.

| Sprint | Days | Delivers | State |
|---|---|---|---|
| 1 — Foundations & RAG | D1–D4 | Skeleton, ingestion, retrieval, RAG ablation | **done** |
| 2 — Agents | D5–D9 | LangGraph, Planner/Editor, sandbox, repair loop, HITL | **in progress** (D7 done) |
| 3 — Guardrails | D10–D11 | Sentinels, policy engine, adversarial suite | planned |
| 4 — Interfaces | D12–D13 | FastAPI + SSE, `forge` CLI, web UI | planned |
| 5 — Integration | D14–D15 | Compose, benchmark, docs, defense | planned |

What works today: AST-aware ingestion, hybrid retrieval (dense + sparse + ripgrep, RRF-fused) with
`file:line` citations, and a grounded question-answering service whose every citation is verified in
code against what was retrieved. Plus the record/replay fixture layer, the multi-provider LLM
factory, the health/search/ask API, a checkpointed multi-turn graph that survives restart, a
citation-backed plan → validated `PatchSet` path that never writes to disk unchecked, and an
execution sandbox that runs tests in an ephemeral container with no network, a read-only root and
memory/CPU/PID caps — with the fallback's gaps written down in
[`docs/limitations.md`](docs/limitations.md). **206 tests green.**

## Quickstart

Runs locally with **no cloud API key** — it answers with the Ollama model on your machine.

```bash
make install
cp .env.local.example .env      # local profile: Ollama (mistral), embedded Qdrant
ollama pull mistral             # if you don't have it already
make test                       # 206 tests, fully offline
make sandbox-image              # optional: build the hardened sandbox image (needs Docker)
```

Without that image — or on a machine with no Docker socket — the sandbox degrades to a documented
`subprocess` fallback that is **not** a security boundary. Every `ExecutionReport` records which one
ran, and the gap is spelled out in [`docs/limitations.md`](docs/limitations.md) §1.

Index a repository, then search and ask it questions — grounded, with citations:

```bash
uv run forge index data/target                          # ingest (sqlparse, ADR-003)
uv run forge search "how are SQL comments stripped"     # hybrid retrieval → path:line
uv run forge ask "how does sqlparse split statements?"  # grounded answer + verified citations
```

Or over HTTP:

```bash
make api                                                # http://localhost:8000/docs
curl -sX POST localhost:8000/v1/ask \
     -H 'content-type: application/json' \
     -d '{"question":"how are statements split?"}' | jq
```

To use a hosted model instead, set `LLM_PROVIDER=gemini` and `GOOGLE_API_KEY` in `.env`. Full stack
under Docker:

```bash
docker compose up                      # qdrant + api
docker compose --profile offline up    # ... plus a local Ollama
```

## How it stays reproducible

Every external call — **LLM completions included** — is recorded to `data/fixtures/` as readable
JSON and replayed under `CACHE_MODE=replay`, where a cache miss is a hard error and nothing touches
the network.

```bash
CACHE_MODE=auto    ...   # read through: serve from disk, else call and record
CACHE_MODE=replay  ...   # disk only. A miss raises. This is what the demo runs.
```

Those fixtures are committed on purpose: a fresh clone with no API keys reproduces the graded demo.
It covers strictly more failure modes than a local-model fallback does — spent quota, provider
outage, and model drift between rehearsal and defense, not just a dead network. Secrets are stripped
on write and never take part in a cache key.

## Configuration

`.env.example` documents every setting. The three that matter most:

| Variable | Why it matters |
|---|---|
| `CACHE_MODE` | `replay` is the offline guarantee. Anything else may hit the network. |
| `LLM_PROVIDER` | `gemini` (default) / `groq` / `ollama`. Three roles per provider — router, reasoner, coder — so the token-fat tiers are used sparingly. |
| `RERANK_ENABLED` | Off. The cross-encoder is measured in the eval harness, not run live — no GPU on the build machine. |

## Deliberate deviations from the cahier

Each is argued in [`docs/descope-v1.md`](docs/descope-v1.md) with its jury-facing justification.
The build machine has ~5 GB of usable RAM and no GPU, and one end-to-end run costs ~29 LLM calls
and ~300k input tokens.

| Cahier | Here | Because |
|---|---|---|
| §7 `AsyncPostgresSaver` | `AsyncSqliteSaver` | Postgres buys multi-writer concurrency, which §3.2 puts out of scope. C4 passes identically. |
| §12.3 eight services | four | C9's test is a clean machine — the examiner's laptop. Four that start beat eight that OOM. |
| §6.5 live reranker | eval harness only | No GPU. The ablation table reports what that costs. |
| §13.3 ten `swe_mini` bugs | four | Quota. Four with honest numbers beats ten extrapolated. |
| §6.1 `docs` corpus | cut | A second ingestion pipeline; the injection demo uses a poisoned repo comment and needs no external docs. |

**Open:** §10.1 mandates React. If the training's own requirement list accepts Streamlit, the web
UI ships as Streamlit and buys back ~2 days. Unresolved — see descope §2.

## Layout

```
src/forge/
├── config.py        settings, model routing, budget + sandbox caps
├── cache/           record/replay fixture store
├── llm/             provider-agnostic chat model factory
├── core/            LangGraph state, agents, the graph        (D5-D9)
├── rag/             ingest, AST chunking, embed, retrieve, answer  (D2-D3)
├── sandbox/         hardened ephemeral executor               (D7)
├── guardrails/      sentinel_in / sentinel_out / policy       (D10)
├── tools/           ripgrep + AST symbols now; git, pytest    (D3, D6-D7)
├── api/             FastAPI: health, search, ask; SSE          (D3, D12)
└── cli/             the `forge` command                       (D12)
```

The cahier's §12.4 splits this across three installable packages. It is one package here, with the
same conceptual boundaries — a solo 14-day build does not repay monorepo overhead.

## Licence

Coursework. No licence granted.
