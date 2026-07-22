# FORGE — Descope v1

**Status:** proposed, awaiting approval
**Date:** 2026-07-22
**Governs:** `Cahier_des_Charges_FORGE.md` (883 lines, 2026-07-22 16:27)

Each change below is a diff against a numbered cahier section. Accepted changes get folded back
into the French cahier; this file then becomes the ADR trail that §15 asks for.

The rule applied throughout: **every cut must be defensible as an engineering decision, not as
"we ran out of time."** A cut you can justify from measurements is a slide. A cut you can't is an
apology.

---

## 0. Why this document exists — the three verified constraints

Measured on the build machine, 2026-07-22:

| Constraint | Measurement |
|---|---|
| RAM | 15 GB total, **5.3 GB available**, 1.3 GB swap already in use |
| GPU | None. `nvidia-smi` fails; Intel UHD Graphics (Raptor Lake-P) only |
| Local models | Ollama holds `mistral:latest` only — **no coder model** |
| Docker | 29.6.2, working. Disk 285 GB free — disk is not a constraint |

And the cost shape of the design itself:

> One end-to-end `change_request` run ≈ **29 LLM calls / ~300k input tokens**, of which ~18 carry a
> 15–20k-token `ContextPack`. That is the cost of *one green run*. Development means dozens per day.

These three — RAM, no GPU, call volume — are what the cahier does not survive contact with.
Nothing else in it is in question.

### Measured on D2, after the stack came up

The RAM estimates above were the pessimistic case. Actuals, `docker compose up`, idle:

| Service | Feared | Measured (idle) |
|---|---|---|
| `qdrant` | 300 MB – 1 GB | **20 MB** |
| `api` | ~500 MB | **41 MB** |

So the four-service topology has far more headroom than §5 assumed. Two caveats keep the descope
standing rather than reversing it:

1. **Idle is not loaded.** BGE-M3 in the API process adds ~2.5 GB resident once retrieval lands, and
   Qdrant grows with the indexed corpus. The measured figure is a floor, not a ceiling.
2. **Quota, not RAM, is the binding constraint.** The ~29 calls / ~300k tokens per run limits
   iteration speed regardless of how much memory is free — so §7's benchmark cuts stand on their own
   reasoning and are not affected by this measurement.

What this *does* soften: the case for dropping `postgres` (§1) rested partly on RAM and partly on
scope. The scope argument is the load-bearing one and is unaffected. Keep SQLite.

One thing this measurement caught that nearly shipped: the API image built at **11.1 GB**, because
torch's default Linux wheel bundles the CUDA runtime — 2.7 GB of NVIDIA libraries that cannot
execute on Intel UHD. Pinning the CPU wheel index took it to **3.08 GB** and the venv from 5.0 GB to
1.2 GB. That would have surfaced on the D14 clean-machine test with no time to fix it.

---

## 1. Memory — §7

| | |
|---|---|
| **Cahier** | `AsyncPostgresSaver`, `thread_id = session_id`; `postgres` service in §12.3 |
| **Change** | `AsyncSqliteSaver`. Drop the `postgres` service. `guardrail_events` moves to the same SQLite file. |
| **Saves** | One compose service (~200 MB resident) |
| **Risk to C4** | None |

**Justification for the defense:** requirement 4.4 asks for short-term conversational memory with
durable checkpointing. SQLite satisfies C4 *identically* — you still kill the container mid-session
and resume from the checkpoint. What Postgres buys over SQLite is concurrent multi-writer access,
and §3.2 already puts multi-user and multi-repo **out of scope**. Choosing storage that matches the
declared scope is a better answer than choosing storage that contradicts it.

`langgraph-checkpoint-sqlite` was already a resolved dependency in the previous slice.

---

## 2. Web UI — §10.1

| | |
|---|---|
| **Cahier** | React + TypeScript + Vite + Tailwind, `react-diff-viewer`/Monaco, nginx service, D13 |
| **Change** | **Streamlit**, served from the `api` container. Drop the `web` service. |
| **Saves** | One compose service, and ~2 days (D13 plus its usual spillover) |
| **Risk to C7** | None — C7 says *"scénario complet exécutable dans le navigateur"*, which Streamlit satisfies |

**This is the single largest saving in this document**, and the one change I cannot fully justify
on my own:

> ⚠️ **Verify before accepting.** FORGE's §10.1 mandates React, but that is *FORGE's own* choice.
> The question is what the **training's** requirement list mandates. My note on the earlier project
> records the training allowing *"Streamlit/React UI"* — if that's right, Streamlit is compliant and
> this change is free. **If the training explicitly requires React, reject this change** and take the
> two days out of §13 instead. Please confirm which it is.

**What is genuinely lost:** the agent timeline and the diff viewer are the two best visual moments
in the demo. Streamlit can do both — `st.status`/`st.empty` for a live agent timeline,
`difflib.unified_diff` into `st.code(..., language="diff")` for per-file diffs — but they will look
plainer than Monaco. Accept that trade knowingly; it buys back D13.

The CLI (§10.2, Typer + Rich) is **unaffected and stays**. It is cheap, it is the honest usage mode
for an engineering assistant, and a Rich live panel is a better agent-timeline demo than either web
option.

---

## 3. Reranker — §6.5

| | |
|---|---|
| **Cahier** | Cross-encoder rerank → top 8, in the live retrieval path |
| **Change** | Reranker **built and measured in the eval harness, disabled in the live path** via config flag |
| **Saves** | Hundreds of ms → seconds per query on CPU; keeps the §13.1 ablation table complete |
| **Risk** | None to any acceptance criterion |

**Justification — and this is the strongest version of this answer, not a concession:** §13 opens by
insisting *« le choix est arbitré par les chiffres »*. So arbitrate it. Row 4 of the §13.1 ablation
table ("AST + hybride + reranker") still gets filled in with a real number. You then ship the
configuration that meets your latency budget and say so:

> "Reranking bought +X nDCG@10 for +Y ms at p95 on CPU. Our latency budget is Z. We ship without it
> and the table shows exactly what that costs us."

That turns the ablation table from decoration into evidence of a decision — which is what makes it
your strongest slide.

---

## 4. Embeddings — §6.3

| | |
|---|---|
| **Cahier** | 2–3 candidates benchmarked: Voyage-family, OpenAI `text-embedding-3-large`, BGE-M3 / Qwen3 |
| **Change** | **One self-hosted model**, chosen for CPU throughput. Benchmark a second only if D4 runs ahead. |
| **Saves** | Paid API keys, and 2 full re-embeddings of the corpus |

**This one is a real quality loss and should be reported as one.** The Voyage and OpenAI candidates
need paid keys; re-embedding the corpus per candidate costs wall-clock on CPU. §6.3's table becomes
*"candidates considered, one measured"* — weaker than what the cahier promises.

Honest framing for the report: *"the embedding model is constrained to self-hosted by the offline
requirement (§12.2); the comparison we could afford within that constraint is in §13.1."* Do not
present a 3-way comparison you did not run.

Pick for CPU throughput and RAM headroom, not for leaderboard position — you have ~5 GB to work
with and the model shares it with Qdrant and the API.

---

## 5. Compose topology — §12.3

| | |
|---|---|
| **Cahier** | 8 services: `api`, `web`, `qdrant`, `postgres`, `redis`, `sandbox`, `indexer`, `ollama` |
| **Change** | **4 services**: `api` (FastAPI + LangGraph + Streamlit), `qdrant`, `sandbox`, and `ollama` behind a non-default `offline` profile |
| **Cut** | `postgres` → §1 · `web` → §2 · `redis` → already #1 on the cahier's own cut list · `indexer` → in-process background task, reachable as `forge index` and `POST /v1/index` |

**Justification for C9:** the acceptance test is *"`docker compose up` sur machine vierge."* The
examiner's laptop is the clean machine. **Four services that start in 5 GB is a better answer to C9
than eight services that OOM in front of the jury.** State the RAM budget explicitly in the README —
showing you sized the deployment to a target is engineering; shipping eight services because the
diagram had eight boxes is not.

---

## 6. Offline fallback — §12.2, and the thing that replaces it

§12.2 says the Ollama profile *« n'est pas optionnel »* — it is the demo-day insurance policy.
Two problems: the coder model **does not exist on this machine yet** (only `mistral:latest`), and a
7B Q4 coder model wants ~4.5 GB resident against 5.3 GB available. It cannot run *alongside* Qdrant
and the API.

**Change — scope the offline profile honestly:**

> The Ollama profile serves the **grounded-Q&A path** (`forge ask`: retrieval + cited answer). It
> does **not** serve the full 6-agent repair loop, which needs the reasoning quality and the context
> window that the hosted models provide.

That is still real insurance for the first half of the demo, and it is truthful. Claiming a local
7B runs the whole repair loop is a claim the jury can falsify in one question.

**And the better insurance policy — port the fixture/replay cache.** The previous slice already
built exactly this and it is recovered and intact:

```
src/travel_planner/cache/fixtures.py    (5.8 KB)   record/replay for external APIs
src/travel_planner/llm/cache.py         (2.5 KB)   record/replay for LLM completions
tests/test_fixture_cache.py, test_llm_cache.py     both green
```

Record every LLM call and every external call during rehearsal; replay them on demo day. Zero
network, zero quota, deterministic, byte-identical to the run you rehearsed. **This is the single
highest-value carry-over from the discarded work** and it is worth more than the Ollama profile,
because it protects against quota exhaustion and API outage as well as network failure.

> **Add to the "never cut" list.** See §11.

---

## 7. Benchmarks — §13.1 and §13.3

| Item | Cahier | Change | Why |
|---|---|---|---|
| Golden set (§13.1) | 60–80 hand-verified pairs | **30–40** | Hand-verification is the expensive step. The ablation needs *relative* deltas between 5 configurations; 30–40 pairs resolves those. |
| `swe_mini` (§13.3) | 10 seeded bugs | **4** | At ~300k tokens/run × repair iterations × re-runs, 10 bugs is the quota killer. |

**Justification:** report `n=4` with real per-task cost and latency, and say why. §13.4 already
establishes the right instinct — *"nommer une limite connue construit plus de crédibilité que
prétendre à la perfection."* Apply it here too: **4 tasks with honest numbers beats 10 with
numbers you had to fabricate or extrapolate.** Keep the harness capable of running N so the
limitation is quota, not code.

The §13.4 security suite (~25 cases) is **unchanged**. It is nearly free — most cases need no LLM
call at all, because the policy engine and the sandbox reject them deterministically. It produces
your headline number. Do not touch it.

---

## 8. Two things I think are wrong in the cahier, independent of budget

Not descope — design critique, since you asked for it.

**8.1 — Cut the `docs` corpus and `web_docs_search` (§6.1, §9).**
Indexing third-party library documentation is a *second complete ingestion pipeline* (URL fetch,
PDF parse, different chunking, different collection) for marginal demo value. The apparent reason to
keep it is the indirect-injection story — but §8.2's demo plants a **poisoned comment in the target
repo**, which needs no external docs at all. Cut the corpus, keep the attack demo intact. Saves
roughly a day of D2. Reinstate only if D2 finishes early.

**8.2 — The RETRIEVER should not always be an LLM call.**
§6.5 opens with *« réécriture (2 à 4 variantes) »* on every query. For a literal symbol query
(`parse_config`, `SessionManager`) query rewriting adds latency and tokens and *degrades* precision
versus feeding the symbol straight to ripgrep and the sparse index. Gate the rewrite: skip it when
the query matches an identifier pattern or the symbol resolves in the AST index.

This is also a *point in your favour* at the defense and it costs nothing to implement — it is the
concrete form of the "grep beats embeddings for exact symbols" judgment the design already claims.

**8.3 — C6 contradicts the cut list.**
§16 C6 requires 10 tools *« exposés en Tools **et** via MCP »*, but §14's cut list item 3 says MCP is
cuttable because the requirement says *« ou »*. Both cannot be true. **Fix C6** to read *"exposés en
Tools LangChain; serveur MCP si le planning le permet"* — otherwise you have written an acceptance
criterion you have pre-authorised yourself to fail.

---

## 9. Target repository — §14 J1

The cahier says 5k–20k LOC. **Take the bottom of that range: ~3–5k LOC.** `ContextPack` size and
therefore token cost scale with repo size, and repo size is the one input to the cost equation you
fully control.

Hard filter, non-negotiable: **the repo must ship a real `pytest` suite.** A4 `SANDBOX_ENGINEER`
extends an existing suite; without one, the red→green demo — the thing the entire value proposition
rests on — has nothing to stand on. Pure Python, no compiled extensions, no heavy install.

Choose it **today**. Everything downstream depends on it and it is the cahier's own §18 action #1.

---

## 10. Schedule re-baseline — §14

The cahier plans 15 days from a standing start. Two facts change that: today is **D2**, and the
repo was reset to empty, so **D1's foundations are undone**. The plan must now fit in **14 days
including today**, not 15.

Compression: fold the cahier's J1 (skeleton, compose, provider keys, ADRs, target-repo choice) into
**today**, and pull the Ollama coder-model pull forward to today as well — it is a multi-GB
download and D14 is the wrong day to discover it.

The recovered `config.py`, `llm/provider.py` (multi-provider factory), the two cache modules, the
`Dockerfile`, and the test scaffolding port across with renames. That is most of J1 already written
and green — roughly half a day back.

---

## 11. Never cut

The cahier's four, endorsed unchanged:

1. Sandbox hardening (§8.3)
2. The guardrail event log (§8.5)
3. The RAG ablation table (§13.1)
4. The clean-machine `docker compose up` test (§14 J14)

Plus one addition, argued in §6:

5. **The fixture/replay cache.** It is what makes the demo unbreakable.

## 12. Revised cut order — decided now, before the pressure

If behind schedule, cut strictly in this order:

1. MCP server (keep LangChain tools — and fix C6 per §8.3 first)
2. Cross-session long-term memory / `langgraph.store`
3. Multi-language support — Python only, drop the TS/TSX chunker
4. Metrics page in the UI (show LangSmith instead)
5. `docs` corpus + `web_docs_search` — if not already cut per §8.1
6. Distinct model families for Editor vs Reviewer — collapse to one provider

---

## Summary of what this buys

| | Cahier | Descoped |
|---|---|---|
| Compose services | 8 | 4 |
| Peak RAM (online path) | Does not fit in 5.3 GB | Fits, with the embedding model in-process |
| Days of UI work | ~2 (D13) | ~0.5 |
| `swe_mini` runs | 10 × iterations | 4 × iterations |
| Acceptance criteria at risk | — | **None**, given the §2 verification and the §8.3 C6 fix |

**No acceptance criterion is dropped.** C1–C10 all still pass. What changes is the implementation
behind C4, C7 and C9, and the sample size behind the evaluation chapter — each with a defensible
reason that is stronger than the thing it replaces.

---

## Open question blocking approval

**§2 — does the training's requirement list mandate React, or does it accept Streamlit?**
Everything else here I can justify from measurements. This one I cannot.
