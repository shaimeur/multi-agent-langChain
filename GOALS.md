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
| C1 | ≥4 specialised agents, distinct responsibilities | `ls src/forge/core/agents/` → 6 files, and `uv run pytest tests/test_agents.py -k distinct` asserting distinct prompt + tool set + output schema per agent | **[x]** |
| C2 | The 5 collaboration forms are demonstrable | `notebooks/02_agent_traces.ipynb` — one annotated run showing handoff, delegation (`needs_more_context`), repair loop, reviewer vote, `interrupt()` | [ ] |
| C3 | Full RAG pipeline, ingestion → grounded generation | `uv run forge index data/target && uv run forge ask "where is X handled"` → answer with `file:line` citations; `uv run pytest evals/test_citations_resolve.py` | **[x]** |
| C4 | Short-term memory works | `scripts/c4_restart_resume.sh` — start a session, `docker compose restart api` mid-run, resume from the checkpoint and get the same thread back | [ ] |
| C5 | Guardrails on input, output and tools | `uv run pytest evals/security -q` green, and `curl -s localhost:8000/v1/guardrails/events \| jq length` > 0 after a run | **[x]** |
| C6 | External tools connected | `uv run forge tools` lists 10, and `uv run pytest tests/test_tools.py -k count` asserts 10 bound. **MCP: see O2 — fix the criterion text before claiming this** | [ ] |
| C7 | Working user interface | The 4-minute §15.6 script run end to end in the browser, screen-recorded to `docs/demo.mp4` | [ ] |
| C8 | API exposed | `curl -s localhost:8000/openapi.json \| jq '.paths \| keys'` shows all §11 routes; `scripts/sse_smoke.sh` streams events | **[x]** |
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
- [x] **Verify one LLM provider key against a live quota** — Gemini key verified live (2026-07-28); the 3.5 tier serves (`gemini-2.5-*` 404 for new keys), runs on `gemini-flash-latest`. A real model now drives RAG + editor/tester/reviewer (`forge ask` grounded; `swe_mini` 3/4). **B2 key-half done**; planner + full `forge fix` graph still scripted-model only
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
- [x] Index the actual target repo; record chunk count, wall clock and index size in `docs/evaluation.md`

**DoD: the target repo is fully indexed; chunk count and time recorded.** **MET** (on D3,
2026-07-23) — `sqlparse` 0.5.5 at `0d24023`: 59 files → 617 chunks, 51.6 s, 3.3 MB on disk
(`docs/evaluation.md`). B1 resolved in ADR-003.

### [x] D3 · Retrieval — DoD met (2026-07-23)

- [x] **Choose the demo target repository** — 3–5k LOC Python (descope §9, bottom of the cahier's range), *must ship a real pytest suite*, no compiled extensions. Clone at a pinned sha into `data/target`, set `TARGET_REPO`, record the choice and the reasoning in a one-paragraph ADR-003 → `sqlparse` 0.5.5 @ `0d24023`, ADR-003. **B1 resolved.**
- [x] `forge index data/target` — record chunks, time and index size in `docs/evaluation.md`; closes D2's DoD
- [x] `rag/retrieve.py` — dense search + sparse search over the named vectors, returning a `SearchHit` carrying score and which retriever found it
- [x] RRF fusion over dense/sparse/ripgrep result lists + payload filters (language, path prefix)
- [x] `tools/ripgrep.py` and `tools/ast_symbols.py` — deterministic literal search and tree-sitter definition/reference lookup
- [x] Gate the query rewrite: identifier-shaped queries (`parse_config`, `SessionManager`) skip the LLM and go straight to ripgrep + sparse (descope §8.2) — cheaper, and *more* precise. *(The routing gate is live; the LLM-rewrite branch it guards is deferred to D5 — no provider yet, B2.)*
- [x] `forge search "..."` — Rich table of `path:line`, symbol, score, which retriever hit it
- [x] Golden set v1: 15 hand-verified `(question, relevant chunk_ids)` pairs in `evals/golden/code.jsonl`. Start here, not on D4 — hand-verification is slower than it looks (cahier §18 action #5)

**DoD: `forge search "where is <a real subsystem> handled"` returns the right files, with baseline
Recall@10 printed.** **MET** (2026-07-23) — `evals/run_retrieval.py` prints Recall@10 = 0.400
(Hit@10 0.40, MRR 0.207) over 15 golden pairs. A low, honest baseline: MiniLM's code weakness and
test-corpus pollution are D4's to fix. `uv run pytest` → 93 passed.

### [x] D4 · RAG evaluation and config freeze — DoD MET (2026-07-23)

- [x] Parent-document expansion (match on the function chunk, generate from the file section) + token-budget packer producing a `ContextPack` — `rag/pack.py`
- [x] Cross-encoder reranker behind `RERANK_ENABLED`, wired into the eval harness only, off in the live path (descope §3) — `rag/rerank.py`
- [x] Golden set to 30–40 verified pairs (descope §7) — **42 pairs** (NL + identifier + multi-chunk)
- [x] Full metric set — Recall@5/@10, Precision@5, MRR, nDCG@10, hit rate, p95 latency. Landed in `forge.evaluation` + `evals/run_ablation.py` (span-overlap scoring); `run_retrieval.py` stays the exact-id D3 baseline. *(Cost/query: $0 — local self-hosted embeddings; latency is the operative cost.)*
- [x] `evals/run_ablation.py` — all 5 §13.1 configs + diagnostics + BGE, one command, emits the markdown table
- [x] Ran the ablation. **MiniLM frozen** on Recall@10 (0.905 vs BGE 0.857) — decision + reranker cost in `docs/evaluation.md` D4
- [ ] ~~RAGAS/deepeval generation metrics~~ — **deferred** (the droppable item): needs a cloud key, B2 still open. Do on D5 once a key lands
- [x] `notebooks/01_rag_evaluation.ipynb` — the ablation with charts (L3, part 1)

**DoD: the §13.1 ablation table is filled with real numbers and the winning config is frozen in
`config.py`.** **MET** — table in `docs/evaluation.md` D4, `EMBEDDING_MODEL` frozen (MiniLM),
`RERANK_ENABLED=false` justified by measured harm. `uv run pytest` → 134 passed.

---

## Sprint 2 — Agents and orchestration (D5–D9)

### [x] D5 · LangGraph skeleton + memory — DoD MET (2026-07-24)

- [x] `core/state.py` — `ForgeState`, the `merge_chunks` reducer (dedup by `chunk_id`, dict-tolerant), `Budget`
- [x] `core/agents/supervisor.py` — `with_structured_output(RouteDecision)`, `Command(goto=...)` routing, budget guard (graceful stop), deterministic fallback for a weak local model. Router tier only
- [x] `core/agents/retriever.py` — wraps D3/D4 (prefer_implementation + parent expansion live); returns a *differential* pack on re-entry
- [x] `core/graph.py` — StateGraph (supervisor → retriever → answer → summary); `AsyncSqliteSaver` with `thread_id = session_id` (descope §1). Sentinel pass-throughs deferred to D10
- [x] Sliding-summary node — folds the oldest turns into a summary + `RemoveMessage`, keeps the last k. Deterministic (no key); LLM summariser is a later refinement
- [x] `forge ask --session` on the graph with `astream` + a Rich live agent-timeline panel; multi-turn via checkpoint resume
- [x] `scripts/c4_restart_resume.sh` — two processes, same session over the SQLite checkpoint (the **C4** proof)

**DoD: multi-turn grounded Q&A with citations, surviving a process restart mid-session.** **MET** —
`uv run pytest tests/test_graph.py` (incl. `test_session_survives_a_process_restart`) green offline;
`uv run pytest` → **143 passed**.

### [x] D6 · Planner + Editor — DoD MET (2026-07-24)

- [x] Schemas in `models.py`: `CitationRef`, `PlanStep` (`is_grounded`), `ChangePlan` (`ungrounded_steps`), `Patch` (search/replace), `PatchSet`
- [x] `core/agents/planner.py` — `with_structured_output(ChangePlan)`; a step whose evidence does not resolve into the ContextPack is dropped before any code is written (grounding enforced in code). Cites snippet numbers, remapped to chunk_ids
- [x] `needs_more_context` → `Command(goto="retriever")` re-entry, with a `max_reentries` cap
- [x] `core/workspace.py` — per-session git worktree under `workspace_root`, realpath-escape-guarded, torn down on close; never touches the pinned clone
- [x] `core/agents/editor.py` — one plan step → a `PatchSet` of structured edits. Never touches disk
- [x] `tools/patch.py` — build a diff from the edits + `apply_patch_dryrun` via `git apply --check` in the worktree; a failing patch never reaches a human

**DoD: a real change request produces a plan and a patch that `git apply --check` accepts — not yet
executed.** **MET** — `pytest tests/test_change.py::test_change_request_yields_a_patch_git_accepts`
(offline, fakes + real worktree). Real-model patch quality is B2-gated. `uv run pytest` → **161 passed**.

### [x] D7 · Sandbox service — DoD met (2026-07-24)

- [x] `docker/sandbox.Dockerfile` — python + pytest + ruff, non-root uid 1000, 160 MB, pip removed after install
- [x] `sandbox/runner.py` — ephemeral container per run via the Docker SDK: `--network=none`, read-only root, writable mount limited to the worktree, `--memory=512m --cpus=1 --pids-limit=128`, hard timeout, output truncation at 64 KB. All 15 flags verified applied via `docker inspect` on a live container
- [x] `ExecutionReport` model (`models.py`) + pytest parser (`sandbox/report.py`): exit code, passed/failed/errored/skipped, failing test ids, stderr tail, duration, coverage percent. **Delta is a two-report subtraction — D8's job, see limitations.md §3**
- [x] `run_pytest` / `run_python` / `run_linter` as LangChain tools returning `ExecutionReport`, never prose. The worktree is bound at construction; targets are escape-checked and a flag is refused as a target
- [x] Documented fallback: `subprocess` + `setrlimit` + process-group kill, with the gap written down in **`docs/limitations.md` §1** (new file, L2 deliverable). **`RLIMIT_DATA` not `RLIMIT_AS`** — measured: ruff SIGABRTed in 8/10 runs under `RLIMIT_AS`, 0/10 under `RLIMIT_DATA`, which still refuses a 1 GB allocation
- [x] Hardening tests: infinite loop killed, fork bomb contained by the pid cap, egress refused, unbounded output capped, memory hog refused, read-only root, non-root uid, host filesystem invisible, API keys not inherited — `tests/test_sandbox.py`, 50 tests

**DoD: pytest runs in the sandbox and returns a structured report; a deliberate infinite loop is
killed cleanly and the API stays up.** → **MET.** `uv run pytest` → **206 passed, 5 skipped**
(the 5 are container-only assertions under the fallback param). Compose contributes the image build
behind a profile; it deliberately does **not** mount the Docker socket — limitations.md §5.

### [ ] D8 · Tester agent + repair loop — DoD met (2026-07-24), 1 task carried

- [x] `core/agents/tester.py` (`SANDBOX_ENGINEER`) — two nodes: `regression` writes the **failing** test first and records whether it actually went red (`regression_red`); `verify` runs the suite. Neither interprets output — the exit code is the verdict
- [x] `implement_loop` subgraph (`core/loop.py`): regression → editor → verify → reviewer → {editor | END}, capped by `max_iterations_per_step`. `apply_patchset` (new, in `tools/patch.py`) writes to the worktree only after `git apply --check` passes, so D6's "the EDITOR never writes" survives intact
- [x] `RevisionRequest` built from the `ExecutionReport` — failing test ids + stderr tail, never a prose complaint. A TIMEOUT is described as a timeout, not as failing tests
- [x] `evals/swe_mini/` — 4 seeded sqlparse bugs (descope §7) + hidden tests the agent never sees; `--limit N` so the ceiling is quota, not code. `--verify` self-checks each bug is seedable/detectable/fixable with **no model**: `uv run python evals/run_swe_mini.py --verify` → **4/4 sound, exit 0**. It earned its keep immediately — it caught SM-03's reverse patch being ambiguous against the `_CaseFilter` base class
- [x] Prove it: broken function repaired in **2 iterations** (< 3). Real worktree, real `git apply`, real pytest in the real Docker sandbox, real `ExecutionReport` driving the revision — **only the model is scripted** (wrong first, right second, so a loop that ignored the evidence would stop at the wrong answer)
- [ ] `notebooks/02_agent_traces.ipynb` — annotated trace of one full loop (L3, part 2). **Carried**: better written against a real-model trace, which is B2-gated

**DoD: a deliberately broken function is repaired autonomously in fewer than 3 iterations.** →
**MET for the mechanism** (`pytest tests/test_repair_loop.py` → 14 passed, exit 0; full suite **229
passed, 5 skipped**). Real-model repair quality is **B2-gated** and unproven — same split as D6. The
swe_mini benchmark is built and self-verified but has not yet been run against a model.

### [x] D9 · Reviewer + human-in-the-loop — DoD met (2026-07-24)

- [x] `core/agents/reviewer.py` — the fixed 5-point checklist, each point a boolean **and** its justification, plus `programmatic` recording *who decided it*. **Three of the five never reach a model**: it is handed a `ReviewJudgement` schema with fields for points 2 and 5 only, so "the tests passed" is not a claim it is able to make. Editor/reviewer family split is `LLMRole.CODER` vs `REASONER`; `shares_family_with_editor()` reports when one provider collapses them rather than implying otherwise
- [x] Grounding runs **in code** against the ContextPack — a citation that was never retrieved fails point 1 regardless of the model's opinion
- [x] `Verdict.APPROVE`/`REVISE` + `ReviewVerdict(checks, feedback, target_step)`, and the routing it drives. `as_revision()` hands the EDITOR each failed point *with its justification* — "the patch did not apply to the worktree" is actionable, "plan_conformance" alone is not
- [x] `interrupt()` at plan approval **and before any patch touches disk**, resumed with `Command(resume=...)`. The patch gate sits between `editor` (build + `git apply --check`) and `apply` (the only node that writes), so rejecting needs no rollback — nothing has happened yet. Resume values are parsed defensively: only an explicit yes approves
- [x] Loop-pathology detection — three disagreements on the same file route to `escalate`, which asks the human, rather than silently ending like the iteration cap
- [x] Budget exhaustion sets `halted` with a readable sentence; no path returns a traceback
- [x] Headless end-to-end through both approval points — `test_the_full_graph_runs_headless_through_both_approval_points`

**DoD: the full graph runs end to end headless with two human approval points.** → **MET.**
Traced: pause 1 `plan_approval` (nothing on disk) → pause 2 `patch_approval` (still nothing on disk,
diff shown) → applied, green, all five checks reported with who decided each.
`uv run pytest` → **265 passed, 5 skipped, exit 0**; `ruff check src tests evals` → exit 0.
Real-model *judgement quality* on points 2 and 5 stays B2-gated — the graph is proven, the critic is not.

**Found on the way (security, not on the D9 list):** LangGraph's default checkpoint serializer
reconstructs any type named in a checkpoint — its own docstring notes that anyone able to write to
the checkpoint DB may thereby trigger code execution. `core/checkpoint.py` now runs it strict with an
allowlist scoped to `forge.models` + `Budget`. Also future-proofs C4 and D9 resume, which the coming
"blocked in a future version" change would otherwise break.

---

## Sprint 3 — Guardrails and hardening (D10–D11)

### [ ] D10 · Guardrails — DoD met (2026-07-24), tier 2 carried

- [x] `guardrails/policy.py` — path whitelist confined to the session worktree, **`os.path.realpath` before every check** (a symlink out is refused, tested), command whitelist of exactly five, `.git`/`.env`/`.ssh` refused even *inside* the tree, git restricted to read-only + apply verbs. Deterministic, pre-LLM. *Per-tool timeouts already live in the D7 sandbox runner, not here*
- [x] `guardrails/sentinel_in.py` — size cap (refuse, never truncate — truncating an attack leaves an attack), in-process sliding-window rate limit (Redis cut, and the per-worker/per-restart consequence is written down), 6 credential shapes redacted, tier-1 injection heuristics
- [ ] **Injection tier 2 — carried.** `classify()` is the seam; the DeBERTa tier is not built. Argued: it would run per-chunk per-pack on a CPU-only box where D4 measured a cross-encoder at 14 ms → 2589 ms p95, and the judge tier needs the key B2 blocks. **Raise with the human** — recorded under open decisions in STATE.md, not silently taken
- [x] `guardrails/injection.py` — spotlighting, instruction stripping (the code around the injection survives; chunks are copied, never mutated, so citations still resolve), base64 payload decoding, **privilege invariance asserted structurally** — the whitelists are literal constants with no code path from a retrieved string to a permission
- [x] `guardrails/sentinel_out.py` — citation re-verification against the ContextPack, secret redaction on generated answers *and* on generated patches, unappliable diffs blocked. Dropping the last citation drops `grounded` with it
- [x] `guardrail_events` table in the checkpoint SQLite + `GET /v1/guardrails/events` and `/summary`, filterable by session/stage/action
- [x] **Wired, not merely importable** — `scan_chunks` runs in the retriever node where third-party text enters the prompt; `check_input`/`check_answer` wrap `POST /v1/ask`

**DoD: every guardrail emits a logged, queryable event.** → **MET**, proven the way C5 asks for it —
live server, `curl .../v1/guardrails/events?session_id=demo | jq length` → **5**, spanning all three
layers (`policy.path_escape`, `policy.command_denied`, `injection.override`,
`injection.exfiltration`, `input.clean`). `uv run pytest` → **322 passed, 5 skipped, exit 0**.

**C5 is half-closed:** the events half is proven; `uv run pytest evals/security -q` is D11's suite.

### [x] D11 · Red team and security suite — DoD met (2026-07-24)

- [x] `evals/security/` — **32 cases** covering all twelve §13.4 attack classes, as pytest. None needs an LLM call. `attacks.py` is the corpus metadata, `test_security.py` attacks through the public surface, `conftest.py` derives the pass rate from the real pytest outcomes
- [x] Sandbox/escape cases: path traversal, symlink escape (realpath), `.env` and `.git/config` reads, SSH key read, absolute-path escape, network egress, fork bomb, infinite loop, memory bomb, unbounded stdout, read-only rootfs. Plus command policy: curl, `bash -c`, `git push`, `git config core.pager`
- [x] Output cases: secret redacted at `sentinel_out`, secret in a generated *patch* blocks the patch, fabricated citation dropped **and the answer's `grounded` flag dropped with it**
- [x] The poisoned-repo scenario end to end — the comment is neutralised, the event fires, and the surrounding code survives so the task still completes
- [x] Fixed what was fixable; **wrote down what was not** — `docs/limitations.md` §6 (direct injection is flagged, not blocked — a deviation from §13.4, argued) and §7 (injection tier 2 is not built)
- [x] `.github/workflows/ci.yml` — lint, sandbox image build, full suite, security suite as its own step, pass rate to the job summary and uploaded as an artifact

**DoD: the security suite is green — or knowingly, documentedly red — with a pass rate you can quote
on a slide.** → **MET: 32/32 attacks mitigated, 0 breached, 2 deliberate deviations named in the
report itself.** The number is computed from the pytest outcomes, so a slide cannot drift from the
suite. `uv run pytest evals/security -q` → exit 0.

---

## Sprint 4 — Interfaces (D12–D13)

### [ ] D12 · FastAPI surface + CLI — DoD met (2026-07-24), `forge review` carried

- [x] All §11 routes — `POST /v1/sessions` (+ `GET`, `DELETE`), `POST .../messages` (SSE), `GET .../history` (replayed from the checkpointer, so it survives a restart), `POST .../approve`, `POST /v1/index` (202 background task — descope §5 folded the indexer service into `api`), `/v1/guardrails/events`, `/v1/metrics`, `/v1/health`
- [x] SSE with `stream_mode=["updates","messages"]` normalised into four typed frames (`node`/`token`/`interrupt`/`done`/`error`) so a client never parses LangGraph's tuples. Every stream ends with a terminal frame — a stream that stops without saying why is indistinguishable from a dropped connection. CORS configured
- [x] Per-session cost/latency/token counters behind `/v1/metrics`, scoped or summed, with guardrail-event counts alongside
- [x] `forge fix` — the change graph with a Rich live timeline, both §5.5 gates rendered as prompts (a plan table, a syntax-highlighted diff), the five-point checklist and the verified diff at the end. A missing key fails with a sentence naming the fix, not a pydantic dump (§9)
- [x] `scripts/sse_smoke.sh` — **all checks pass**: the §11 route table, session lifecycle, SSE framing with a terminal frame, guardrail events, metrics. Uses curl + jq, so it proves the surface from outside the process
- [ ] `forge review` — **carried.** `forge fix` already renders the reviewer's checklist; a standalone review-only command wants a real model to be worth anything (B2)

**DoD: the complete workflow is drivable from the terminal with streamed output.** → **MET.**
`uv run pytest` → **340 passed, 5 skipped, exit 0**; `./scripts/sse_smoke.sh` → exit 0.
**C8 closes.** On a key-less machine the streamed run ends in a typed `error` frame (B2) — the
channel and framing are proven; a full multi-frame run is proven in tests with a scripted graph.

**Found on the way:** `build_llm` installs a *process-global* LangChain cache and never removed it,
so any test touching a real provider silently routed every later `FakeListChatModel` through the
fixture store — a FixtureMiss in an unrelated file. `reset_llm_cache()` + an autouse fixture close it.

### [ ] D13 · Web UI — **React (O1 resolved 2026-07-24: the cahier's original)**

- [x] Resolve **O1** — *"If React is mandatory, this day becomes two and something from the cut list goes."* It is. Budget accordingly: D13 is the two-day version and only three days remain
- [x] `web/` — Vite + TS + Tailwind, a separate build from the Python package (not served by `api`); `npm run build` (`tsc -b && vite build`) → exit 0, oxlint clean
- [x] Streamed chat against `POST /v1/sessions/{id}/messages` — the typed SSE frames (`node`/`token`/`interrupt`/`done`/`error`) are parsed off a `fetch` ReadableStream (EventSource can't POST); tokens stream into a live bubble
- [x] Agent activity timeline off the `node` frames — the visual proof of multi-agent
- [x] Plan-approval modal and patch-approval modal → `POST /v1/sessions/{id}/approve`; the `interrupt` frame carries the payload each renders (plan table / coloured diff)
- [x] Diff viewer, test-results panel (red → green), sessions sidebar, **citations panel**, guardrail
      event panel, cost panel, `Index repo` button, and an explicit `Ask | Change` mode (see O8)
- [ ] ~~Metrics page~~ — cut-list item 4. `GET /v1/metrics` exists if the day runs long enough to use it

**DoD: the full §15.6 demo scenario runs in the browser with no terminal.**
**Depends on B2** — the browser scenario needs a real model, not a scripted one.
**Status (2026-08-03):** the §15.6 scenario was driven in a real browser against a live API, a real
model and the docker sandbox. **8 of 9 beats seen on screen** — index, grounded citations, bug report,
agent timeline, plan gate, red tests, patch gate + diff, and the guardrail firing on the planted
comment; the repair loop closed too (reviewer `revise` → a better patch). **Not yet seen:** the Cost
panel rendering, and beat 6's *green* half — the daily quota hit `429` mid-repair. Both are a few
spare requests away, not new code. Three defects the browser found are fixed (`2870ad8`, `04726cc`);
three new open items are in STATE.md as **O6** (`/v1/ask` skips the §8.2 injection scan — security),
**O7** (retrieval cannot bridge a call hop) and **O8** (the UI, not the SUPERVISOR, picks ask vs change).

---

## Sprint 5 — Integration and defense (D14–D15)

### [ ] D14 · Containerisation, benchmark, documentation

- [x] Multi-stage Dockerfile (node stage builds `web/`, api stage serves it), git + ripgrep installed, entrypoint bootstraps the target repo, `API_PORT` overridable
- [x] **`scripts/clean_machine_test.sh`** written — clones HEAD into an empty dir, `cp .env.example .env`, `docker compose up`, then probes health/openapi/SPA/index/search and restarts the container to prove C4. **It already revealed five defects, all fixed** (no `git` in the image, no `ripgrep`, no target repo in a clone, `.env.example` defaulting to `CACHE_MODE=auto`, no UI served at all). **The run itself is still pending** — this sandbox's Docker daemon has no registry egress, so `node:22-slim` cannot be pulled (**C9 open**)
- [ ] Run `swe_mini` (4 bugs): resolution rate, mean repair iterations, cost and wall clock per task, regression rate → `docs/evaluation.md` — needs quota
- [x] README rewritten: one-command quickstart, "use it on your own project", API table, honest "what does not work yet". Stale status claims deleted
- [x] `make requirements` → `requirements.txt` (383 lines)
- [x] `docs/architecture.md` written; `docs/limitations.md` already existed. Folding descope back into the cahier is still **O3** (frozen file — human)

**DoD: one command brings the whole system up on a clean machine, and the benchmark numbers are
recorded.**

### [ ] D15 · Slides, rehearsal, freeze

- [x] 12 slides per §15.5 drafted in `docs/slides.md`, with the four certain jury questions answered in an annexe. Slide 6 carries the real ablation numbers
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
| ~~§10.1 React + TS + Vite + Tailwind, `web` service~~ | **NOT DESCOPED — O1 resolved 2026-07-24 (human): build React as the cahier specifies.** The Streamlit fallback is withdrawn and its `ui` extra dropped from pyproject; `web/` becomes a separate build | The proposed Streamlit swap bought ~2 days at the cost of Monaco-quality diffs. The human took the cahier's original. **Note the consequence: D13 is now the two-day version with three days left, so D14/D15 are tight — see the cut list** |
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
