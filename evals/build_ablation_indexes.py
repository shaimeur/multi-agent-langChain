"""Build the alternate collections the §13.1 ablation needs — once, idempotently.

The live ``code`` collection (AST chunks, MiniLM) already exists from
``forge index``. The ablation additionally needs:

  * ``code_naive``  — naive ~1000-char chunks, MiniLM  → row 1, the baseline to beat
  * ``code_bge``    — AST chunks, BGE-M3               → the MiniLM-vs-BGE decision

Building ``code_bge`` is slow: BGE-M3 embeds at ~1.7 s/chunk on this CPU-only
machine (~35 min for the target repo), which is exactly why the default is MiniLM
(docs/evaluation.md, D2). So indexing is this separate step, not part of every
eval run — build once, then re-run ``run_ablation.py`` as often as needed.

    uv run python evals/build_ablation_indexes.py            # code_naive (fast)
    uv run python evals/build_ablation_indexes.py --bge      # + code_bge (~35 min)
    uv run python evals/build_ablation_indexes.py --bge --force
"""

from __future__ import annotations

import argparse
import time

from forge.config import get_settings
from forge.rag import store
from forge.rag.chunkers import chunk_file, chunk_naive
from forge.rag.embed import SentenceTransformerEmbedder
from forge.rag.ingest import ChunkFn, index_repo

MINILM = "sentence-transformers/all-MiniLM-L6-v2"
BGE = "BAAI/bge-m3"


def build(client, settings, repo, collection, embedder, chunk_fn: ChunkFn, force: bool) -> None:
    existing = store.count(client, collection)
    if existing and not force:
        print(f"  {collection:16s} exists ({existing} chunks) — skip (--force to rebuild)")
        return
    started = time.perf_counter()
    report = index_repo(
        repo,
        settings=settings,
        client=client,
        embedder=embedder,
        collection=collection,
        chunk_fn=chunk_fn,
        full=True,
    )
    print(
        f"  {collection:16s} {report.chunks} chunks in "
        f"{time.perf_counter() - started:.1f}s  ({embedder.name})"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the ablation's alternate collections.")
    parser.add_argument(
        "--bge", action="store_true", help="also build code_bge (AST, BGE-M3, ~35 min)"
    )
    parser.add_argument(
        "--naive-bge", action="store_true", help="also build code_naive_bge (naive, BGE-M3)"
    )
    parser.add_argument(
        "--force", action="store_true", help="rebuild even if the collection exists"
    )
    args = parser.parse_args()

    settings = get_settings()
    repo = settings.target_repo
    client = store.build_client(settings)
    print(f"Target repo: {repo}")

    build(
        client,
        settings,
        repo,
        "code_naive",
        SentenceTransformerEmbedder(MINILM),
        chunk_naive,
        args.force,
    )
    if args.bge:
        build(
            client,
            settings,
            repo,
            "code_bge",
            SentenceTransformerEmbedder(BGE),
            chunk_file,
            args.force,
        )
    if args.naive_bge:
        build(
            client,
            settings,
            repo,
            "code_naive_bge",
            SentenceTransformerEmbedder(BGE),
            chunk_naive,
            args.force,
        )
    print("done.")


if __name__ == "__main__":
    main()
