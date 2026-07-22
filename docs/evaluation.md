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

## D3 — retrieval quality

Pending. Golden set + the §13.1 ablation:

| Configuration | Recall@10 | nDCG@10 | p95 latency |
|---|---|---|---|
| Naive character chunking + dense only | | | |
| AST chunking + dense only | | | |
| AST + hybrid (RRF) | | | |
| AST + hybrid + reranker | | | |
| + parent expansion | | | |

The reranker row gets filled in and then **shipped disabled** — see `descope-v1.md` §3. The table is
what justifies that, so it has to be measured, not assumed.

---

## D14 — end-to-end

Pending `swe_mini` (4 seeded bugs, cut from 10 — `descope-v1.md` §7): resolution rate, mean repair
iterations, cost and wall-clock per task, regression rate.

---

## Infrastructure baseline

Measured with `docker compose up`, idle, before any corpus is loaded:

| Service | Resident |
|---|---:|
| `api` | 41 MB |
| `qdrant` | 20 MB |

API image is 3.08 GB — down from 11.1 GB before torch was pinned to the CPU wheel index, which
removed 19 CUDA packages that cannot execute on this hardware. The venv went 5.0 GB → 1.2 GB.
