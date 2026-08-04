# Evaluation

Measurements, not estimates. Cahier §13.

Machine for every number here: Intel Raptor Lake-P, **no NVIDIA driver — CPU only**, 15 GB RAM.
That constraint is the point: it is also roughly what the examiner's laptop looks like.

---

## D2 — ingestion baseline

Corpus: the FORGE repository itself, indexed with `forge index .`.

| Metric | Value |
|---|---|
| Files walked | 34 |
| Chunks produced | 345 |
| Chunks per file | 10.1 |
| Vector dimensions | 1024 (BGE-M3) / 384 (MiniLM) |
| Collection | `code`, named `dense` + `sparse` vectors in one collection |

### Embedding throughput — the finding that changed the default

Same 345 chunks, batch size 32, CPU:

| Model | Dim | Load | Embed | ms/chunk | Full index of a 5k-LOC repo* |
|---|---:|---:|---:|---:|---:|
| `sentence-transformers/all-MiniLM-L6-v2` | 384 | 6.6 s | 16.2 s | **47** | ~1 min |
| `BAAI/bge-m3` | 1024 | 5.7 s | 589.7 s | **1709** | ~35 min |

<sub>*extrapolated at ~1200 chunks, the expected size of a 5k-LOC target repo.</sub>

**36×.** This is not a tuning detail, it changes what is possible:

1. **Development pace.** D3–D8 involve reindexing constantly. At 1.7 s/chunk a full reindex is a
   coffee break; at 47 ms it is a pause. Over ten remaining days that difference compounds into
   more than the entire time budget for the RAG sprint.
2. **It breaks a demo moment the cahier explicitly plans for.** §14/J2 promises *"an incremental
   reindex takes 2 seconds and looks professional."* A one-file change is ~5–10 chunks — 0.5 s with
   MiniLM, 9–17 s with BGE-M3. Only one of those is the demo that was written down.
3. **Storage and RAM.** 1024 vs 384 dimensions is 2.7× the vector memory in Qdrant for the same
   corpus.

**Decision:** the default is now a fast 384-dim model, and BGE-M3 stays configurable via
`EMBEDDING_MODEL`. This is a *provisional* decision made on throughput alone.

**What has not been measured yet:** retrieval quality. MiniLM is a general-purpose English model and
is expected to be weaker on code than BGE-M3. D4's ablation over the golden set decides the final
answer on **Recall@10 and nDCG@10**, and it is entirely possible that BGE-M3 earns its cost back —
in which case the demo is run against a pre-warmed index and the slow path is documented rather than
hidden.

This is exactly the arbitration §6.3 of the cahier asks for: *« le choix est arbitré par les
chiffres »*. Half the numbers are in.

---

## D3 — retrieval

### Target repository index (closes D2's DoD)

`sqlparse` 0.5.5, pinned at `0d24023`, cloned into `data/target` (ADR-003), indexed with
`forge index data/target --full`:

| Metric | Value |
|---|---|
| Files walked | 59 |
| Chunks produced | 617 |
| Wall clock (incl. 6.6 s model load) | 51.6 s |
| Index on disk (embedded Qdrant) | 3.3 MB |
| BM25 vocabulary | 3 650 terms |
| Embedder | all-MiniLM-L6-v2, 384-dim |

D2's definition of done — *"the target repo is fully indexed; chunk count and time recorded"* —
is met here. It was blocked on B1 (repo unchosen), not on the pipeline, exactly as D2 recorded.

### Retrieval baseline — the D3 DoD number

15 hand-verified `(question, chunk_ids)` pairs in `evals/golden/code.jsonl`, scored by
`uv run python evals/run_retrieval.py` over the live hybrid retriever (dense + sparse, RRF fusion):

| Metric | Value |
|---|---|
| Recall@10 | **0.400** |
| Hit@10 | 0.400 |
| MRR | 0.207 |

Six of fifteen. Two findings, and D4 is the place to act on both:

1. **MiniLM is weak on code** — exactly what the D2 switch-note predicted when it chose the model on
   throughput alone and deferred quality to the ablation. 0.40 is that quality's first measurement.
   D4 decides MiniLM vs BGE-M3 on *this* number, not on the 36× throughput gap.
2. **The test corpus outranks the implementation.** For *"how are comments stripped"* the five
   `test_strip_comments_*` functions fill the top of the ranking and bury `StripCommentsFilter`
   below rank 10 — a descriptive test name matches the query better than the code it exercises.
   Levers, all on D4: a path/language filter that prefers implementation over tests, parent-document
   expansion, or the reranker that is already built for the harness.

The §13.1 ablation table is filled with real numbers in the **D4** section below.

---

## D4 — RAG evaluation and config freeze

The never-cut deliverable (`descope-v1.md` §11). One command, `uv run python evals/run_ablation.py`,
runs every configuration over the golden set and emits the tables below; `notebooks/01_rag_evaluation.ipynb`
charts them.

### The metric: (path, line-span) overlap, not exact chunk-id

The D3 baseline scored an exact `chunk_id` match: Recall@10 = **0.400**. That cannot score the naive
row at all — fixed-character windows produce different ids for the same lines — so the ablation scores
a **span overlap**: a retrieved chunk covers a relevant answer when they share a file and their line
ranges intersect. Every id hit is also a span hit, so this is a superset of the 0.400 number, not a
softer one, and it is the only metric under which naive and AST chunking are comparable. The golden
set grew from 15 to **42 hand-verified pairs** (`descope-v1.md` §7), mixing natural-language questions,
identifier-shaped queries, and multi-chunk answers.

### The §13.1 ablation table — MiniLM (the shipped embedder)

| Configuration | Recall@10 | nDCG@10 | p95 latency |
|---|---|---|---|
| Naive char chunking + dense | 0.655 | 0.418 | 10 ms |
| AST chunking + dense | 0.857 | 0.559 | 7 ms |
| AST + hybrid (RRF) | 0.750 | 0.596 | 14 ms |
| AST + hybrid + reranker | 0.774 | 0.547 | 2589 ms |
| + parent expansion | 0.786 | 0.547 | 2345 ms |

Full metrics (span overlap, 42 golden pairs):

| Configuration | R@5 | R@10 | P@5 | MRR | nDCG@10 | hit@10 | chunks/pack | p95 |
|---|---|---|---|---|---|---|---|---|
| Naive char chunking + dense | 0.512 | 0.655 | 0.114 | 0.369 | 0.418 | 0.67 | 10 | 10 ms |
| AST chunking + dense | 0.643 | 0.857 | 0.181 | 0.474 | 0.559 | 0.88 | 10 | 7 ms |
| AST + hybrid (RRF) | 0.714 | 0.750 | 0.205 | 0.561 | 0.596 | 0.76 | 10 | 14 ms |
| AST + hybrid + reranker | 0.631 | 0.774 | 0.148 | 0.494 | 0.547 | 0.79 | 10 | 2589 ms |
| + parent expansion | 0.667 | 0.786 | 0.148 | 0.494 | 0.547 | 0.79 | 31 | 2345 ms |

**AST chunking is the differentiator.** Naive → AST (dense only) lifts Recall@10 from 0.655 to
**0.857** and nDCG@10 from 0.418 to 0.559 — the cahier's central claim (*"the generic 1000-character
pipeline behaves badly on source code"*), measured. **Hybrid** raises the top of the ranking
(R@5 0.643→0.714, MRR 0.474→0.561, nDCG 0.559→0.596) but Recall@10 *dips* to 0.750 — the pollution
below.

### The test-corpus pollution, and the lever that fixes it (the D3 finding)

For *"how are comments stripped"*, BM25 ranks the five `test_strip_*` functions above the
`StripCommentsFilter` they exercise, and unweighted RRF fuses those tests into the top-10.
`prefer_implementation` (in `retrieve.Filters`) demotes — never drops — test chunks below
implementation after fusion: deterministic, model-free.

| Configuration | R@5 | R@10 | MRR | nDCG@10 | hit@10 | chunks/pack | p95 |
|---|---|---|---|---|---|---|---|
| AST + hybrid (baseline) | 0.714 | 0.750 | 0.561 | 0.596 | 0.76 | 10 | 14 ms |
| AST + hybrid + prefer-impl | 0.774 | **0.869** | 0.593 | **0.652** | 0.88 | 10 | 35 ms |
| AST + hybrid + prefer-impl + parent expansion | 0.786 | **0.905** | 0.593 | 0.652 | 0.90 | 35 | 20 ms |

The lever takes Recall@10 past dense-only (0.750 → 0.869) for ~35 ms and no model. **Parent-document
expansion** on top reaches **Recall@10 = 0.905 / hit@10 = 0.90** — the enclosing class is handed to the
generator, so a split answer such as `TokenList` (two chunks) is covered whole. Its cost is pack size:
mean chunks per query 10 → 35, bounded by the token-budget packer in production.

### Why the reranker ships disabled (descope §3, arbitrated by numbers)

The cross-encoder (`ms-marco-MiniLM-L-6-v2`) takes p95 from **14 ms → 2589 ms** on this CPU, and being
a general web-search model it *degrades* the ranking on code: versus plain hybrid it **lowers** R@5
(0.714 → 0.631), MRR (0.561 → 0.494) and nDCG@10 (0.596 → 0.547). The free `prefer_implementation`
lever beats it outright. So it is built and measured, and shipped `RERANK_ENABLED=false` — the table is
the evidence, which is a stronger slide than shipping it silently.

### The embedder decision — MiniLM vs BGE-M3 (cahier §6.3, "arbitrated by numbers")

`code_bge` = the target repo re-embedded with BGE-M3 (617 chunks, **742 s** at ~1.7 s/chunk vs MiniLM's
~20 s). Both embedders run through the *same* shipped pipeline (AST + hybrid + `prefer_implementation`
+ parent expansion):

| Embedder | R@5 | Recall@10 | MRR | nDCG@10 | hit@10 | p95 / query | index time |
|---|---|---|---|---|---|---|---|
| **MiniLM (384-dim)** | 0.786 | **0.905** | 0.593 | 0.652 | **0.90** | **20 ms** | **~20 s** |
| BGE-M3 (1024-dim) | 0.738 | 0.857 | **0.673** | **0.706** | 0.86 | 233 ms | 742 s |

The two headline metrics **split**: MiniLM wins **Recall@10** (0.905 vs 0.857) and hit@10; BGE-M3 wins
**nDCG@10** (0.706 vs 0.652) and MRR — it ranks the right chunk a little higher *within* the top-k. The
tiebreaker is what this system does with the result: the grounded-answer path feeds the whole top-k to
the LLM and verifies citations **by content**, so *whether the evidence is in the pack* (recall) matters
more than *where in the pack it sits* (nDCG/MRR). MiniLM wins recall — at **11× lower query latency and
37× lower index time**, the latter preserving the cahier §14/J2 "2-second incremental reindex" demo, on
a CPU-only box whose ~5 GB is shared with Qdrant and the API.

**Decision: freeze MiniLM** (`all-MiniLM-L6-v2`, 384-dim), frozen in `config.py`, no longer provisional.
This *reverses* the D3 hypothesis on evidence: the 0.400 baseline was a weak **pipeline**, not a weak
**embedder** — MiniLM reaches 0.905 once AST chunking, hybrid retrieval, test-demotion and parent
expansion are in place, each a bigger lever than the embedder swap. BGE-M3 stays configurable via
`EMBEDDING_MODEL` for a GPU deployment where its nDCG edge costs nothing.

### The frozen configuration

- **Embedder** — `all-MiniLM-L6-v2` (384-dim), arbitrated above.
- **Retrieval** — AST chunking · hybrid dense + BM25 with RRF · `prefer_implementation` demotion ·
  parent-document expansion under a token budget. Best measured **Recall@10 = 0.905, nDCG@10 = 0.652**,
  up from the D3 exact-id baseline of 0.400.
- **Reranker** — built and measured, shipped `RERANK_ENABLED=false`.
- Two levers are staged for D5: the live `forge ask` path adopts `prefer_implementation=True` when the
  Retriever node lands and the offline mistral fixture is re-recorded (today it is proven in the
  harness and defaulted in `config.py`, but not yet flipped under the committed demo fixture).

---

## D14 — end-to-end

`swe_mini`, 4 seeded bugs (cut from 10 — `descope-v1.md` §7), run 2026-08-04 against
`gemini-3.5-flash` (editor) and `gemini-flash-latest` (reviewer), sandbox on the docker backend,
`CACHE_MODE=auto`. Target repo `sqlparse@0d24023`.

### The harness self-check comes first

`uv run python evals/run_swe_mini.py --verify` → **exit 0, all four sound**. It needs no model: it
proves each bug's hidden test is green on clean code, red once seeded, and green again after the
reference fix. A score without this is a measurement of the harness, not of the agent — and a target
repo bump is exactly what silently breaks it.

| bug | defect | verdict |
|---|---|---|
| SM-01 | `remove_quotes` leaves the closing quote behind | sound |
| SM-02 | `strip_semicolon` only removes one trailing token | sound |
| SM-03 | `identifier_case` rewrites quoted identifiers | sound |
| SM-04 | `truncate_strings` keeps one character too few | sound |

### The run

`uv run python evals/run_swe_mini.py` → **exit 0, 4/4 repaired**.

| bug | status | iterations | hidden test |
|---|---|---:|---|
| SM-01 | REPAIRED | 1 | `exit=0` 1 passed in 0.59s |
| SM-02 | REPAIRED | 1 | `exit=0` 1 passed in 0.51s |
| SM-03 | REPAIRED | 1 | `exit=0` 2 passed in 0.62s |
| SM-04 | REPAIRED | 1 | `exit=0` 1 passed in 0.51s |

| metric | value |
|---|---:|
| Resolution rate | **4/4 (100%)** |
| Regression rate | **0/4 (0%)** |
| Mean repair iterations | **1.0** |
| Wall clock | 80 s total, **~20 s/task** |
| LLM calls | 11 total, **2.75/task** |
| Tokens | 20 613 total (9 868 in / 10 745 out), **~5 150/task** |

The 11 calls were recorded as fixtures, so the whole benchmark **replays with the network off**:
`CACHE_MODE=replay … run_swe_mini.py` → exit 0, the same 4/4, in **25 s** rather than 80. The 55 s
difference is the provider latency, and the replay is what makes this table re-runnable in front of
a jury on a dead network.

`REPAIRED` is the strict verdict: the hidden test passed **and** the target's full suite still
passed. A fix that bought the hidden test at the cost of a regression grades `REGRESSED`, not
`REPAIRED`, so the 0% regression rate is carried by the same four results rather than asserted
separately.

### Three things this number does not say

**It is not an end-to-end score — retrieval is bypassed.** `run_swe_mini.py` builds the
`ContextPack` directly from the seeded file (`the pack stands in for retrieval`, its own comment).
So 4/4 measures the planner→editor→sandbox→reviewer loop *given the correct file*. It is not what
the full system scores from a bug report alone: the O7 limitation in `limitations.md` records that
for SM-01 the report names `get_real_name` while the defect is two call hops down in
`utils.remove_quotes`, absent from the top 35 retrieved chunks. End-to-end, SM-01 would fail at
retrieval before the loop ever saw it. The honest reading is that this table scores repair, and §D3
scores retrieval, and the system is the weaker of the two.

**Mean iterations of 1.0 means the repair loop never had to iterate.** Every first patch passed both
review and the hidden test, so this run exercises the loop's happy path only. The evidence that the
repair path *works* is the §15.6 browser run, where the reviewer returned `revise` on a red sandbox
result and the second patch was better — not this benchmark.

**The §4/A5 "critic off the editor's family" check passed on a technicality.** It compares
configured model-name strings; `gemini-flash-latest` and `gemini-3.5-flash` differ as strings, so
the harness printed no warning, but both are Gemini Flash. The critic very likely still shares the
editor's blind spots. Splitting the two roles across two model IDs was done to split the free-tier
per-model quota, and it should not be read as genuine model diversity.

### Why four bugs and not ten

`descope-v1.md` §7. The ceiling is free-tier quota, not code — `--limit N` runs any subset, and this
run cost 11 calls against a ~20/day/model allowance. Four bugs with numbers that were actually
measured beat ten extrapolated from one.

---

## Infrastructure baseline

Measured with `docker compose up`, idle, before any corpus is loaded:

| Service | Resident |
|---|---:|
| `api` | 41 MB |
| `qdrant` | 20 MB |

API image is 3.08 GB — down from 11.1 GB before torch was pinned to the CPU wheel index, which
removed 19 CUDA packages that cannot execute on this hardware. The venv went 5.0 GB → 1.2 GB.
