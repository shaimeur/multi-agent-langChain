# ADR-002 — Qdrant, with an embedded escape hatch

**Status:** accepted · **Date:** 2026-07-22 · **Cahier:** §6.4

## Context

Code retrieval needs three things a plain dense index does not give: sparse/lexical matching
(identifiers are matched literally, not semantically), payload filtering (`language = 'python' AND
path LIKE 'src/%'`), and two corpora sharing one deployment.

## Decision

**Qdrant**, with dense and sparse vectors in one collection and RRF fusion over both.

The client is configured by `QDRANT_URL`. When set — the compose default — it talks to the Qdrant
container. When blank it falls back to embedded mode against `QDRANT_PATH`, same API, no service.

## Alternatives

| Option | Rejected because |
|---|---|
| **Chroma** | Simplest to start, but weaker payload filtering and no first-class sparse vectors. We would hand-roll the half of the pipeline that matters most for code. |
| **pgvector** | One fewer service *if* Postgres were already there — but ADR-003 drops Postgres, so it would be adding a service, not saving one. Hybrid search would be hand-rolled. |
| **FAISS** | No payload filtering, no persistence story, no sparse. |

## The escape hatch, and why it exists

The build machine has ~5 GB of usable RAM and the online stack must also hold the embedding model.
If Qdrant-as-a-service turns out not to fit beside everything else, `QDRANT_URL=""` drops a
container with no code change.

Recording this now rather than discovering it on D14 is the point. The clean-machine compose test is
an acceptance criterion (C9) and the examiner's laptop is the clean machine.

## Consequences

- Two code paths for the client. Kept honest by running the retrieval tests against both.
- Sparse vectors mean the BM25 index is Qdrant's concern, not a second store to keep in sync —
  which is precisely why `bm25s` stays confined to the eval harness for baseline comparison.
