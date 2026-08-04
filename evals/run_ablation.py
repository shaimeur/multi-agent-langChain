"""The §13.1 RAG ablation — every configuration in one command, one table.

    uv run python evals/build_ablation_indexes.py         # once: build code_naive
    uv run python evals/run_ablation.py                   # the MiniLM tables
    uv run python evals/build_ablation_indexes.py --bge   # once: build code_bge (~35 min)
    uv run python evals/run_ablation.py                   # now also the BGE decision rows

Three tables come out:

  * **canonical** — the five §13.1 rows (naive → AST → +hybrid → +reranker →
    +parent expansion), MiniLM, scored by (path, line-span) overlap so the naive
    row is comparable to the rest;
  * **diagnostic** — the test-corpus-demotion lever the D3 finding motivates;
  * **bge** — the same configs under BGE-M3, for the embedder decision.

Rows whose collection has not been built are skipped with a note, so the fast
MiniLM tables are available before the slow BGE index exists. Everything lands in
``evals/results/`` for the notebook and docs/evaluation.md.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from forge.config import PROJECT_ROOT, get_settings
from forge.evaluation.harness import (
    RetrievalConfig,
    build_span_index,
    evaluate_config,
    load_golden,
)
from forge.models import Chunk, ChunkKind, SearchHit
from forge.rag import store
from forge.rag.embed import Embedder, SentenceTransformerEmbedder
from forge.rag.rerank import Reranker, build_reranker
from forge.rag.retrieve import Filters

MINILM = "sentence-transformers/all-MiniLM-L6-v2"
BGE = "BAAI/bge-m3"
RESULTS_DIR = PROJECT_ROOT / "evals" / "results"

_IMPL = Filters(prefer_implementation=True)


def _embedder_model(collection: str) -> str:
    return BGE if collection.endswith("_bge") else MINILM


def build_matrix(client) -> list[RetrievalConfig]:
    """The configs whose collections exist, in canonical → diagnostic → bge order."""
    have = lambda c: store.count(client, c) > 0  # noqa: E731
    rows: list[RetrievalConfig] = []

    # --- canonical: the five §13.1 rows, MiniLM ---
    if have("code_naive"):
        rows.append(
            RetrievalConfig(
                "Naive char chunking + dense", "code_naive", "dense", embedder_model=MINILM
            )
        )
    rows += [
        RetrievalConfig("AST chunking + dense", "code", "dense", embedder_model=MINILM),
        RetrievalConfig("AST + hybrid (RRF)", "code", "hybrid", embedder_model=MINILM),
        RetrievalConfig(
            "AST + hybrid + reranker", "code", "hybrid", rerank=True, embedder_model=MINILM
        ),
        RetrievalConfig(
            "+ parent expansion",
            "code",
            "hybrid",
            rerank=True,
            parent_expand=True,
            embedder_model=MINILM,
        ),
    ]

    # --- diagnostic: the test-corpus-demotion lever, and the best rerank-free build ---
    rows += [
        RetrievalConfig(
            "AST + hybrid + prefer-impl",
            "code",
            "hybrid",
            filters=_IMPL,
            embedder_model=MINILM,
            tag="diagnostic",
        ),
        RetrievalConfig(
            "AST + hybrid + prefer-impl + parent exp.",
            "code",
            "hybrid",
            parent_expand=True,
            filters=_IMPL,
            embedder_model=MINILM,
            tag="diagnostic",
        ),
        # O7 / limitations.md §8. The row above is the shipped live configuration, so
        # this one differs from it by exactly one knob — which is what makes the delta
        # readable as the cost and benefit of the call hop and nothing else.
        RetrievalConfig(
            "  + one-hop call expansion",
            "code",
            "hybrid",
            parent_expand=True,
            expand_calls=True,
            filters=_IMPL,
            embedder_model=MINILM,
            tag="diagnostic",
        ),
    ]

    # --- bge: the embedder decision, same configs under BGE-M3 ---
    if have("code_naive_bge"):
        rows.append(
            RetrievalConfig(
                "Naive char chunking + dense",
                "code_naive_bge",
                "dense",
                embedder_model=BGE,
                tag="bge",
            )
        )
    if have("code_bge"):
        # Names mirror their MiniLM twins so the notebook can pair them by name.
        rows += [
            RetrievalConfig(
                "AST chunking + dense", "code_bge", "dense", embedder_model=BGE, tag="bge"
            ),
            RetrievalConfig(
                "AST + hybrid (RRF)", "code_bge", "hybrid", embedder_model=BGE, tag="bge"
            ),
            RetrievalConfig(
                "AST + hybrid + prefer-impl",
                "code_bge",
                "hybrid",
                filters=_IMPL,
                embedder_model=BGE,
                tag="bge",
            ),
            RetrievalConfig(
                "AST + hybrid + prefer-impl + parent exp.",
                "code_bge",
                "hybrid",
                parent_expand=True,
                filters=_IMPL,
                embedder_model=BGE,
                tag="bge",
            ),
        ]
    return rows


def canonical_table_md(summaries: list[dict[str, Any]]) -> str:
    """The exact §13.1 table shape: Configuration | Recall@10 | nDCG@10 | p95 latency."""
    lines = ["| Configuration | Recall@10 | nDCG@10 | p95 latency |", "|---|---|---|---|"]
    for s in summaries:
        lines.append(
            f"| {s['name']} | {s['recall@10']:.3f} | {s['ndcg@10']:.3f} | {s['p95_ms']:.0f} ms |"
        )
    return "\n".join(lines)


def full_table_md(summaries: list[dict[str, Any]]) -> str:
    """Every §13.1 metric, for docs/evaluation.md."""
    cols = "| Configuration | R@5 | R@10 | P@5 | MRR | nDCG@10 | hit@10 | chunks | p50 | p95 |"
    lines = [cols, "|---|---|---|---|---|---|---|---|---|---|"]
    for s in summaries:
        lines.append(
            f"| {s['name']} | {s['recall@5']:.3f} | {s['recall@10']:.3f} "
            f"| {s['precision@5']:.3f} | {s['mrr']:.3f} | {s['ndcg@10']:.3f} "
            f"| {s['hit@10']:.2f} | {s['mean_chunks']:.0f} | {s['p50_ms']:.0f} ms "
            f"| {s['p95_ms']:.0f} ms |"
        )
    return "\n".join(lines)


def _print(title: str, summaries: list[dict[str, Any]], console: Console) -> None:
    table = Table(title=title, header_style="bold")
    table.add_column("Configuration")
    for col in ("R@5", "R@10", "P@5", "MRR", "nDCG@10", "hit@10", "chk", "p50", "p95"):
        table.add_column(col, justify="right")
    for s in summaries:
        table.add_row(
            s["name"],
            f"{s['recall@5']:.3f}",
            f"{s['recall@10']:.3f}",
            f"{s['precision@5']:.3f}",
            f"{s['mrr']:.3f}",
            f"{s['ndcg@10']:.3f}",
            f"{s['hit@10']:.2f}",
            f"{s['mean_chunks']:.0f}",
            f"{s['p50_ms']:.0f}ms",
            f"{s['p95_ms']:.0f}ms",
        )
    console.print(table)


def _warm(embedders: dict[str, Embedder], reranker: Reranker | None) -> None:
    """Load model weights before the timed loop so latency reflects steady state,
    not a one-off cold start landing on whichever query ran first."""
    for emb in embedders.values():
        emb.embed_query("warmup")
    if reranker is not None:
        fake = SearchHit(
            chunk=Chunk(
                chunk_id="0" * 16,
                repo="w",
                path="w.py",
                language="python",
                kind=ChunkKind.FUNCTION,
                start_line=1,
                end_line=1,
                text="def w(): pass",
                raw="def w(): pass",
            ),
            score=1.0,
        )
        reranker.rerank("warmup", [fake], top_k=1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the §13.1 RAG ablation.")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR / "ablation.json")
    args = parser.parse_args()

    console = Console()
    settings = get_settings()
    client = store.build_client(settings)

    golden = load_golden()
    span_index = build_span_index(client, store.CODE_COLLECTION)
    console.print(f"[dim]{len(golden)} golden queries · {len(span_index)} indexed chunks[/]")

    configs = build_matrix(client)
    models_needed = {_embedder_model(c.collection) for c in configs}
    embedders: dict[str, Embedder] = {m: SentenceTransformerEmbedder(m) for m in models_needed}
    reranker = build_reranker(settings) if any(c.rerank for c in configs) else None
    _warm(embedders, reranker)

    summaries: list[dict[str, Any]] = []
    for cfg in configs:
        console.print(f"[dim]· {cfg.name} [{cfg.tag}] on {cfg.collection}[/]")
        summaries.append(
            evaluate_config(
                cfg,
                golden,
                span_index,
                settings=settings,
                client=client,
                embedder=embedders[_embedder_model(cfg.collection)],
                reranker=reranker,
                repo=settings.target_repo,
            )
        )

    console.print()
    by_tag = {
        tag: [s for s in summaries if s["tag"] == tag] for tag in ("canonical", "diagnostic", "bge")
    }
    _print("§13.1 ablation · MiniLM · span-overlap", by_tag["canonical"], console)
    if by_tag["diagnostic"]:
        _print("Diagnostic — test-corpus demotion (D3 finding)", by_tag["diagnostic"], console)
    if by_tag["bge"]:
        _print("Embedder decision — BGE-M3", by_tag["bge"], console)

    console.print("\n[bold]§13.1 canonical table (MiniLM, the shipped embedder):[/]\n")
    console.print(canonical_table_md(by_tag["canonical"]))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    (RESULTS_DIR / "ablation_table.md").write_text(
        canonical_table_md(by_tag["canonical"]) + "\n", encoding="utf-8"
    )
    (RESULTS_DIR / "ablation_full.md").write_text(full_table_md(summaries) + "\n", encoding="utf-8")
    console.print(f"\n[green]wrote[/] {args.out}")


if __name__ == "__main__":
    main()
