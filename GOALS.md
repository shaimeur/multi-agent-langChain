# FORGE — build plan

The working checklist. `docs/cahier-des-charges.md` §14 is the backbone; every deviation from it is
argued in `docs/descope-v1.md` and recorded in the [descope register](#descope-register) below. Where
the two disagree, **this file describes what is actually being built.**

---

## How to use this file

1. Read **`docs/STATE.md`** — the session-start hook prints it for you. It is the resume point;
   nothing else needs re-reading first.
2. Take **the first unchecked box**. Do not shop the list.
3. Do it. Tick it. Commit with the task text as the message.
4. Update `docs/STATE.md` in the same commit — or run `/checkpoint`, which does it for you.
5. **When a day's DoD fails, cut from the [cut list](#cut-list) that same evening.** Not tomorrow, not
   "I'll make it up on the weekend." Scope rolled forward silently is how a 15-day plan becomes an
   18-day plan with three days of unfinished work at the end.

Ticking an [acceptance gate](#acceptance-gates) means you ran its proof command and it passed. It does
not mean the feature feels done.

---

## Where I am

The live resume point — current day, next concrete action, blockers, open decisions, and things not
to redo — lives in **`docs/STATE.md`**, rewritten at every `/checkpoint` and printed into context at
session start by the `.claude/` session-start hook. GOALS.md is the durable plan; STATE.md is where
you are in it right now.

---

## Never cut these four

The project is judged on them. They are not negotiable against schedule pressure.

1. **Sandbox hardening** (§8.3) — D7
2. **The guardrail event log** (§8.5) — D10
3. **The RAG ablation table** (§13.1) — D4
4. **The clean-machine `docker compose up` test** (§14 J14) — D14

Plus one added by descope §6, for the same reason:

5. **The fixture/replay cache.** It is what makes the demo unbreakable — already built, keep it green.

---

## Acceptance gates

C1–C10 from cahier §16. Each row's proof is a command you run or an artifact that exists. Tick only
after running it.

| # | Criterion | Proof | Done |
|---|---|---|---|
| C1 | ≥4 specialised agents, distinct responsibilities | `ls src/forge/core/agents/` → 6 files, and `uv run pytest tests/test_agents.py -k distinct` asserting distinct prompt + tool set + output schema per agent | [ ] |
| C2 | The 5 collaboration forms are demonstrable | `notebooks/02_agent_traces.ipynb` — one annotated run showing handoff, delegation (`needs_more_context`), repair loop, reviewer vote, `interrupt()` | [ ] |
| C3 | Full RAG pipeline, ingestion → grounded generation | `uv run forge index data/target && uv run forge ask "where is X handled"` → answer with `file:line` citations; `uv run pytest evals/test_citations_resolve.py` | [ ] |
| C4 | Short-term memory works | `scripts/c4_restart_resume.sh` — start a session, `docker compose restart api` mid-run, resume from the checkpoint and get the same thread back | [ ] |
| C5 | Guardrails on input, output and tools | `uv run pytest evals/security -q` green, and `curl -s localhost:8000/v1/guardrails/events \| jq length` > 0 after a run | [ ] |
| C6 | External tools connected | `uv run forge tools` lists 10, and `uv run pytest tests/test_tools.py -k count` asserts 10 bound. **MCP: see O2 — fix the criterion text before claiming this** | [ ] |
| C7 | Working user interface | The 4-minute §15.6 script run end to end in the browser, screen-recorded to `docs/demo.mp4` | [ ] |
| C8 | API exposed | `curl -s localhost:8000/openapi.json \| jq '.paths \| keys'` shows all §11 routes; `scripts/sse_smoke.sh` streams events | [ ] |
| C9 | Containerised deployment | `scripts/clean_machine_test.sh` — fresh clone into an empty dir, `cp .env.example .env && docker compose up`, all healthchecks green, on a machine that has never seen the project | [ ] |
| C10 | Deliverables complete | L1 cahier · L2 repo+README+requirements.txt+ADRs+evaluation.md+limitations.md · L3 two notebooks · L4 Dockerfiles+compose · L5 live demo + video · L6 12 slides | [ ] |

---

## Sprint 1 — Foundations and RAG (D1–D4)

### [x] D1 · Framing, skeleton, decisions — DoD met, 3 tasks carried

- [x] Write the cahier des charges (L1) — `docs/cahier-des-charges.md`, 883 lines
- [x] Write `docs/descope-v1.md` — every deviation argued against a numbered cahier section
- [x] Package skeleton, `pyproject.toml`, `Makefile`, `.env.example`, `.dockerignore`
- [x] `config.py` — settings, 3-role model routing, budget caps, sandbox caps, `secret_values()`
- [x] Port the record/replay fixture cache for external calls and LLM completions
- [x] Multi-provider LLM factory (`init_chat_model`, gemini/groq/ollama × router/reasoner/coder)
- [x] `GET /v1/health` + `forge config`
- [x] `docker-compose.yml` — 4 services, not 8 (descope §5); healthchecks with `service_healthy`
- [x] Pin torch to the CPU wheel index — image 11.1 GB → 3.08 GB, venv 5.0 → 1.2 GB
- [x] ADR-001 (agent decomposition), ADR-002 (vector store)
- [ ] **Choose the demo target repository** — carried to D3 #1. This is **B1**
- [ ] **Verify one LLM provider key against a live quota** — no `.env` exists. This is **B2**
- [ ] **Pull the Ollama coder model** (`qwen2.5-coder:7b`, ~4.5 GB) — this is **B3**

**DoD: `docker compose up` starts Qdrant + the API and the health probe answers.** Met — idle RSS
measured for both services in `docs/evaluation.md`, `tests/test_api.py` green. Postgres is absent by
decision (descope §1), not by omission.

### [ ] D2 · Ingestion and indexing

- [x] Repo walker — `.gitignore` via `git ls-files`, `.forgeignore`, skip dirs/lockfiles/binaries, 512 KB cap
- [x] tree-sitter AST chunker for Python — function/method/class/module boundaries, oversized splitting
- [x] Metadata enrichment header (file, lang, class, imports, docstring) embedded but not shown in diffs
- [x] `parent_id` stored per chunk, for D4's parent-document expansion
- [x] Prose fallback chunker for Markdown
- [x] `Embedder` protocol + sentence-transformers implementation + hashing test double
- [x] BM25 sparse encoder with persisted IDF statistics
- [x] Qdrant store — named `dense` + `sparse` vectors in one collection, payload indexes, embedded-mode fallback
- [x] `forge index <path>`, incremental by default via `git diff --name-only`, `--full` to rebuild
- [x] Measure embedding throughput and switch the default on the number — 47 vs 1709 ms/chunk, `docs/evaluation.md`
- [ ] ~~tree-sitter chunker for TS/TSX~~ — **cut**, cut-list item 3, taken early. `walker.LANGUAGES` is Python + prose only
- [ ] Index the actual target repo; record chunk count, wall clock and index size in `docs/evaluation.md`

**DoD: the target repo is fully indexed; chunk count and time recorded.** **NOT MET** — blocked on
B1, not failed. The pipeline is complete and measured (34 files → 345 chunks, 16.2 s), but against
FORGE itself. It converts to met within an hour of choosing the repo.

> **Response to the miss:** do not cut yet. The cheapest fix is D3 #1, which is the next box anyway.
> If the target repo is still unchosen at the end of the next session, take cut-list item 1 (MCP
> server) that evening — and fix C6 first, per O2.

### [ ] D3 · Retrieval

- [ ] **Choose the demo target repository** — 3–5k LOC Python (descope §9, bottom of the cahier's range), *must ship a real pytest suite*, no compiled extensions. Clone at a pinned sha into `data/target`, set `TARGET_REPO`, record the choice and the reasoning in a one-paragraph ADR-003
- [ ] `forge index data/target` — record chunks, time and index size in `docs/evaluation.md`; closes D2's DoD
- [ ] `rag/retrieve.py` — dense search + sparse search over the named vectors, returning a `SearchHit` carrying score and which retriever found it
- [ ] RRF fusion over dense/sparse/ripgrep result lists + payload filters (language, path prefix)
- [ ] `tools/ripgrep.py` and `tools/ast_symbols.py` — deterministic literal search and tree-sitter definition/reference lookup
- [ ] Gate the query rewrite: identifier-shaped queries (`parse_config`, `SessionManager`) skip the LLM and go straight to ripgrep + sparse (descope §8.2) — cheaper, and *more* precise
- [ ] `forge search "..."` — Rich table of `path:line`, symbol, score, which retriever hit it
- [ ] Golden set v1: 15 hand-verified `(question, relevant chunk_ids)` pairs in `evals/golden/code.jsonl`. Start here, not on D4 — hand-verification is slower than it looks (cahier §18 action #5)

**DoD: `forge search "where is <a real subsystem> handled"` returns the right files, with baseline
Recall@10 printed.**

### [ ] D4 · RAG evaluation and config freeze — *do not sacrifice this day*

- [ ] Parent-document expansion (match on the function chunk, generate from the file section) + token-budget packer producing a `ContextPack`
- [ ] Cross-encoder reranker behind `RERANK_ENABLED`, wired into the eval harness only, off in the live path (descope §3)
- [ ] Golden set to 30–40 verified pairs (descope §7, down from 60–80)
- [ ] `evals/run_retrieval.py` — Recall@5/@10, Precision@5, MRR, nDCG@10, hit rate, p95 latency, cost per query
- [ ] `evals/run_ablation.py` — all 5 §13.1 configurations in one command, emitting the markdown table
- [ ] Run the ablation. Decide MiniLM vs BGE-M3 **on Recall@10 and nDCG@10**, not on throughput. Freeze the winner in `config.py` and write the decision — including the cost of the reranker you are shipping without — into `docs/evaluation.md`
- [ ] RAGAS or deepeval baseline: faithfulness, answer relevancy, context precision/recall, plus a citation-precision metric *(the droppable item if the day runs long)*
- [ ] `notebooks/01_rag_evaluation.ipynb` — the ablation with charts (L3, part 1)

**DoD: the §13.1 ablation table is filled with real numbers and the winning config is frozen in
`config.py`.**

---

## Sprint 2 — Agents and orchestration (D5–D9)

### [ ] D5 · LangGraph skeleton + memory

- [ ] `core/state.py` — `ForgeState`, the `merge_chunks` reducer (dedup by `chunk_id`), `Budget`
- [ ] `core/agents/supervisor.py` — `with_structured_output(RouteDecision)`, intent classification, `Command(goto=...)` routing, budget guard. Router tier only, never free text
- [ ] `core/agents/retriever.py` — wraps D3/D4 retrieval; on re-invocation returns a *differential* pack, not the whole thing again
- [ ] `core/graph.py` — assemble the StateGraph with `answer_node` and sentinel pass-throughs; wire `AsyncSqliteSaver` with `thread_id = session_id` (descope §1)
- [ ] Sliding-summary node — past N tokens, fold the oldest turns into one `SystemMessage`, keep the last k verbatim
- [ ] `forge ask` on the graph with `astream` and a Rich live agent panel; multi-turn against the target repo
- [ ] `scripts/c4_restart_resume.sh` — kill the process mid-session, resume from the checkpoint (this is the **C4** proof)

**DoD: multi-turn grounded Q&A with citations, surviving a process restart mid-session.**

### [ ] D6 · Planner + Editor

- [ ] Schemas in `models.py`: `CitationRef`, `PlanStep`, `ChangePlan`, `Patch`, `PatchSet`
- [ ] `core/agents/planner.py` — `with_structured_output(ChangePlan)`; **a step whose `evidence` does not resolve into the ContextPack is rejected before any code is written**
- [ ] `needs_more_context` → `Command(goto="retriever")` re-entry, with a re-entry cap so it cannot ping-pong
- [ ] `core/workspace.py` — per-session git worktree under `workspace_root`, created on session start, torn down on close
- [ ] `core/agents/editor.py` — one plan step → a `PatchSet` of structured edits. Never touches disk
- [ ] `tools/patch.py` — `apply_patch_dryrun` via `git apply --check` inside the worktree; a patch that fails the check never reaches a human

**DoD: a real change request produces a plan and a patch that `git apply --check` accepts — not yet
executed.**

### [ ] D7 · Sandbox service — *hardest infra day, protect it*

- [ ] `docker/sandbox.Dockerfile` — python + pytest + ruff, non-root user, no shell tooling that is not needed
- [ ] `sandbox/runner.py` — ephemeral container per run via the Docker SDK: `--network=none`, read-only root, writable mount limited to the worktree, `--memory=512m --cpus=1 --pids-limit=128`, hard timeout, output truncation at 64 KB
- [ ] `ExecutionReport` model + a pytest output parser: exit code, passed/failed/errored, failing test names, stderr tail, duration, coverage delta
- [ ] `run_pytest` / `run_python` / `run_linter` as LangChain tools returning `ExecutionReport`, never prose
- [ ] Documented fallback: `subprocess` + `resource.setrlimit` + timeout when the Docker socket is unavailable, with the security gap written down rather than hidden (risk register)
- [ ] Hardening tests: infinite loop killed, fork bomb contained, network egress refused, 10 GB of stdout truncated without taking the API down

**DoD: pytest runs in the sandbox and returns a structured report; a deliberate infinite loop is
killed cleanly and the API stays up.**

### [ ] D8 · Tester agent + repair loop

- [ ] `core/agents/tester.py` (`SANDBOX_ENGINEER`) — generates or extends pytest; for a bug fix it writes the **failing** regression test first
- [ ] `implement_loop` subgraph: Editor → Tester → Reviewer(stub) → Editor, capped by `max_iterations_per_step`
- [ ] `RevisionRequest` built from the `ExecutionReport` — failing test names and stderr as evidence, not a prose complaint
- [ ] Seed `evals/swe_mini/` with **4** realistic bugs in the target repo (descope §7, down from 10) plus a hidden test suite; keep the harness able to run N so the limit is quota, not code
- [ ] Prove it: one deliberately broken function repaired autonomously in under 3 iterations
- [ ] `notebooks/02_agent_traces.ipynb` — annotated trace of one full loop (L3, part 2)

**DoD: a deliberately broken function is repaired autonomously in fewer than 3 iterations.**

### [ ] D9 · Reviewer + human-in-the-loop

- [ ] `core/agents/reviewer.py` — the fixed 5-point checklist, each point returning a boolean *and* its justification. Different model family from the Editor (until cut-list item 6 is taken)
- [ ] Grounding check runs **in code**: every cited `file:line` resolves into the ContextPack via `ContextPack.supports()`. The LLM's opinion is not consulted
- [ ] `APPROVE` / `REVISE(feedback, target_step)` schema and the routing it drives
- [ ] `interrupt()` at plan approval and again before any patch touches disk; resume via `Command(resume=...)`
- [ ] Supervisor loop-pathology detection — Editor and Reviewer disagreeing 3× on the same file escalates to the human instead of burning budget
- [ ] Budget exhaustion returns a graceful partial answer, never a stack trace
- [ ] Headless end-to-end run through both approval points, scripted

**DoD: the full graph runs end to end headless with two human approval points.**

---

## Sprint 3 — Guardrails and hardening (D10–D11)

### [ ] D10 · Guardrails

- [ ] `guardrails/policy.py` — path whitelist confined to the session worktree, **`os.path.realpath` before every check**, command whitelist (never a blacklist), per-tool timeouts. Deterministic, pre-LLM
- [ ] `guardrails/sentinel_in.py` — Pydantic schema + size limits, in-process rate limit (no Redis, cut-list item 1 already taken), secret scan on user input, cheap heuristic injection rules
- [ ] Injection tier 2: classifier over the heuristics, LLM judge only on the ambiguous middle
- [ ] `guardrails/injection.py` — spotlighting (`<untrusted_context>` wrapper), instruction stripping on retrieved chunks, privilege invariance: retrieved text can never alter tool permissions or path policy
- [ ] `guardrails/sentinel_out.py` — schema revalidation, `git apply --check`, citation verification, secret redaction on generated code
- [ ] `guardrail_events` table in the checkpoint SQLite file + `GET /v1/guardrails/events`

**DoD: every guardrail emits a logged, queryable event — "here are the 47 guardrail events from this
session", not "we have guardrails".**

### [ ] D11 · Red team and security suite

- [ ] `evals/security/` — the ~25 §13.4 cases as pytest. Most need no LLM call at all
- [ ] Sandbox escape cases: path traversal, symlink escape, `.env` / `.git/config` read, network egress, fork bomb, infinite loop, memory bomb, 10 GB stdout
- [ ] Output cases: secret in generated code redacted at `sentinel_out`; fabricated citation detected and flagged
- [ ] The poisoned-repo scenario end to end — plant the comment, watch the event fire, watch the task complete normally anyway
- [ ] Fix what is fixable; **write down what is not** in `docs/limitations.md`
- [ ] GitHub Actions workflow running the suite on push

**DoD: the security suite is green — or knowingly, documentedly red — with a pass rate you can quote
on a slide.**

---

## Sprint 4 — Interfaces (D12–D13)

### [ ] D12 · FastAPI surface + CLI

- [ ] All §11 routes: `POST /v1/sessions`, `POST /v1/sessions/{id}/messages`, `GET .../history`, `POST .../approve`, `POST /v1/index`, `GET /v1/guardrails/events`, `GET /v1/metrics`
- [ ] SSE streaming of LangGraph events with `stream_mode=["updates","messages"]`, typed error handling, CORS
- [ ] Cost, latency and token counters behind `/v1/metrics`, accumulated per session
- [ ] `forge fix` and `forge review` finished, with the Rich live agent-activity panel
- [ ] `scripts/sse_smoke.sh` + an OpenAPI route check (this is the **C8** proof)

**DoD: the complete workflow is drivable from the terminal with streamed output.**

### [ ] D13 · Web UI — *Streamlit pending O1*

- [ ] Resolve **O1** before writing a line of this. If React is mandatory, this day becomes two and something from the cut list goes
- [ ] Streamlit app served from the `api` container: streamed chat
- [ ] Agent activity timeline (`st.status` / `st.empty`) — the visual proof of multi-agent
- [ ] Plan approval control → `POST /v1/sessions/{id}/approve`
- [ ] Diff viewer — `difflib.unified_diff` into `st.code(..., language="diff")`, per file
- [ ] Citations panel, test results panel (red → green), sessions sidebar
- [ ] ~~Metrics page~~ — cut-list item 4; show LangSmith instead if the day runs long

**DoD: the full §15.6 demo scenario runs in the browser with no terminal.**

---

## Sprint 5 — Integration and defense (D14–D15)

### [ ] D14 · Containerisation, benchmark, documentation

- [ ] Multi-stage Dockerfiles for api and sandbox, healthchecks, compose profiles, final pass
- [ ] **`scripts/clean_machine_test.sh`** — fresh clone into an empty directory on a machine that has never seen the project: `cp .env.example .env && docker compose up`. Fix everything it reveals; it always reveals something (**C9**)
- [ ] Run `swe_mini` (4 bugs): resolution rate, mean repair iterations, cost and wall clock per task, regression rate → `docs/evaluation.md`
- [ ] README rewrite: architecture diagram, quickstart, env var reference, API reference, evaluation results, limitations. Delete the stale status claims
- [ ] `make requirements` → `requirements.txt` (the cahier asks for it; pyproject stays the source of truth)
- [ ] `docs/architecture.md` and `docs/limitations.md` finished; fold the approved descope decisions back into the cahier (**O3**)

**DoD: one command brings the whole system up on a clean machine, and the benchmark numbers are
recorded.**

### [ ] D15 · Slides, rehearsal, freeze

- [ ] 12 slides per §15.5. Slide 6 (the ablation table) is the strongest one — build it first
- [ ] Pre-warmed demo state: target repo pre-indexed, caches warm, poisoned-comment file staged, `CACHE_MODE=replay` verified end to end with the network physically off
- [ ] **Record the video of a successful run.** Non-negotiable. Do this before rehearsing, not after
- [ ] Rehearse the 4-minute script three times end to end, stopwatch in hand
- [ ] Prepare the four certain jury questions: why multi-agent over one agent with tools · how do you know it does not hallucinate · what if the model writes malicious code · what does a request cost
- [ ] Re-run the clean-machine test on the morning of D15 (risk register: Docker breaking overnight)
- [ ] **Code freeze at noon.** The afternoon is rehearsal only

**DoD: three clean rehearsals, a recorded fallback video, and a frozen tree.**

---

## Cut list

Priority order, decided in advance so the decision is never made under pressure. Descope §12 revises
the cahier's §14 order.

| # | Cut | Status |
|---|---|---|
| 1 | MCP server — keep the LangChain tools. **Fix C6 first** (O2) | available |
| 2 | Cross-session long-term memory / `langgraph.store` | available |
| 3 | Multi-language — Python only, drop the TS/TSX chunker | **taken** on D2 |
| 4 | Metrics page in the UI — show LangSmith instead | available |
| 5 | `docs` corpus + `web_docs_search` | **taken** pre-emptively, descope §8.1 |
| 6 | Distinct model families for Editor vs Reviewer — collapse to one provider | available |
| — | Redis (rate limiting moves in-process) | **taken** at D1, cahier's own item 1 |

---

## Descope register

What the spec says, what is being built, and the argument. Sourced from `docs/descope-v1.md`; status
O3 — approved in code, not yet folded back into the cahier.

| Spec says | Built as | Because |
|---|---|---|
| §7 `AsyncPostgresSaver` + a `postgres` service | `AsyncSqliteSaver`, `guardrail_events` in the same file | Postgres buys multi-writer concurrency; §3.2 puts multi-user out of scope. C4 passes identically. Choosing storage that matches the declared scope beats storage that contradicts it |
| §10.1 React + TS + Vite + Tailwind, `web` service | Streamlit served from `api` — **pending O1** | C7 asks for "a full scenario runnable in the browser", which Streamlit satisfies. Buys back ~2 days. Loses Monaco-quality diffs, knowingly |
| §6.5 cross-encoder rerank in the live path | Built and measured in the eval harness, shipped disabled | No GPU. §13 insists the choice is arbitrated by numbers — so the ablation reports what disabling it costs, and that is a stronger slide than shipping it silently |
| §6.3 2–3 embedding candidates benchmarked | One self-hosted model, chosen on measured CPU throughput; second only if D4 runs ahead | Voyage and OpenAI need paid keys, and re-embedding per candidate costs wall clock on CPU. **A real quality loss — report it as one.** Do not present a 3-way comparison that was never run |
| §12.3 eight compose services | Four: `api`, `qdrant`, `sandbox` (D7), `ollama` behind an `offline` profile | C9's test is a clean machine — the examiner's laptop. Four services that start beat eight that OOM in front of the jury |
| §12.2 Ollama profile serves the whole system | Ollama serves the grounded-Q&A path only; the **fixture/replay cache** is the real demo insurance | A 7B Q4 coder wants ~4.5 GB against ~5.3 GB available and cannot sit beside Qdrant and the embedder. Replay covers spent quota and provider outage too, not just a dead network |
| §13.1 golden set of 60–80 pairs | 30–40 | The ablation needs *relative* deltas across 5 configs; 30–40 resolves those. Hand-verification is the expensive step |
| §13.3 ten seeded `swe_mini` bugs | Four | ~300k tokens per run × repair iterations × re-runs. 4 with honest numbers beats 10 extrapolated. The harness stays able to run N |
| §6.1 two corpora (`code` + `docs`) | `code` only; `web_docs_search` dropped | A second full ingestion pipeline (URL fetch, PDF parse, different chunking) for marginal value. The injection demo uses a poisoned repo comment and needs no external docs |
| §12.4 three installable packages | One package, same conceptual boundaries | A solo 14-day build does not repay monorepo overhead |
| §6.5 query rewrite on every query | Rewrite gated — identifier-shaped queries go straight to ripgrep + sparse | Rewriting a literal symbol adds latency and tokens and *degrades* precision. This is the concrete form of "grep beats embeddings for exact symbols" |
| §16 C6 "Tools **and** MCP" | Unresolved — **O2** | §14's cut list says MCP is cuttable because the requirement says "or". Both cannot be true. Fix the criterion or drop the cut |

---

## Risk watchlist

| Risk | Trigger — how you know it is happening | Response |
|---|---|---|
| Target repo never gets chosen | It is still unchosen at the end of the next session | Stop. Pick any 3–5k-LOC Python repo with a pytest suite that you already know. A merely adequate repo chosen today beats a perfect one chosen on D6 |
| No working LLM provider | D5 opens and there is still no `.env` with a verified key | Half a day, hard stop, before anything else on D5. Every agent day is blocked behind it |
| Sandbox day overruns | D7 ends without pytest running in a container | Take the documented `subprocess` + `setrlimit` fallback that evening, write the security gap into `docs/limitations.md`, move on. Do not spend D8 on it |
| LLM quota exhausted mid-sprint | A day's development burns more than ~15 end-to-end runs, or a 429 appears | Switch to `CACHE_MODE=replay` for everything that is not the change under test; drop `swe_mini` from 4 bugs to 2 before dropping anything else |
| Repair loop does not converge | The same test fails 3 iterations running on the same file | The iteration cap already exists — verify it fires and escalates, rather than raising it |
| RAG quality is poor on the target repo | D4's ablation shows Recall@10 below ~0.6 across every configuration | Change the target repo on D4, while there is still time. This is why the ablation is on D4 and not D12 |
| Scope drift | You are writing something that appears in no box in this file | Stop and either add the box or delete the work |
| Network dies on defense day | — | Fixtures + `CACHE_MODE=replay` + the recorded video. Verified on D15, not assumed |
| Docker breaks overnight before defense | The morning re-run of the clean-machine test fails | The recorded video is the deliverable. Do not debug Docker at 9 a.m. |
