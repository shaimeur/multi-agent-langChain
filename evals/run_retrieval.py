"""Baseline retrieval metrics over the hand-verified golden set — cahier §13.1.

Recall@k, Hit@k and MRR against ``evals/golden/code.jsonl`` using the live hybrid
retriever. This is D3's single baseline number; D4 wraps the same ``evaluate`` in
the five-configuration ablation, which is why it takes injectable search kwargs
rather than reaching for a global.

    uv run python evals/run_retrieval.py            # against the indexed target repo
    uv run python evals/run_retrieval.py --k 5

Relevance is keyed by ``chunk_id``, which is stable across re-embedding (it hashes
repo/path/symbol/line, not content), so the same golden file scores every
embedding candidate in the ablation without re-verification.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from forge.config import Settings, get_settings
from forge.rag import store
from forge.rag.embed import Embedder, build_embedder
from forge.rag.retrieve import hybrid_search, load_encoder

GOLDEN = Path(__file__).parent / "golden" / "code.jsonl"


def load_golden(path: Path = GOLDEN) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def evaluate(
    k: int = 10,
    golden: list[dict] | None = None,
    *,
    settings: Settings | None = None,
    embedder: Embedder | None = None,
    **search_kwargs: Any,
) -> dict[str, Any]:
    """Run every golden query and aggregate.

    The client, embedder and BM25 encoder are built once and reused across all
    queries — embedded Qdrant permits a single client per path, and rebuilding the
    embedder per query would reload the model weights each time. ``search_kwargs``
    pass through to ``hybrid_search`` so D4's ablation can vary fusion and filters.
    """
    settings = settings or get_settings()
    golden = golden if golden is not None else load_golden()
    client = store.build_client(settings)
    embedder = embedder or build_embedder(settings)
    encoder = load_encoder(settings)

    per_query = []
    for item in golden:
        relevant = set(item["relevant"])
        retrieved = [
            h.chunk.chunk_id
            for h in hybrid_search(
                item["question"],
                k=k,
                settings=settings,
                client=client,
                embedder=embedder,
                encoder=encoder,
                **search_kwargs,
            )
        ]
        found = relevant.intersection(retrieved)
        rr = next(
            (1.0 / rank for rank, cid in enumerate(retrieved, start=1) if cid in relevant), 0.0
        )
        per_query.append(
            {
                "id": item.get("id"),
                "question": item["question"],
                "answer": item.get("answer", ""),
                "recall": len(found) / len(relevant) if relevant else 0.0,
                "hit": 1.0 if found else 0.0,
                "rr": rr,
            }
        )
    n = len(per_query) or 1
    return {
        "n": len(per_query),
        "k": k,
        "recall": sum(q["recall"] for q in per_query) / n,
        "hit": sum(q["hit"] for q in per_query) / n,
        "mrr": sum(q["rr"] for q in per_query) / n,
        "per_query": per_query,
    }


def _print(report: dict[str, Any], console: Console) -> None:
    k = report["k"]
    table = Table(
        title=f"Retrieval baseline · {report['n']} golden queries · k={k}", header_style="bold"
    )
    table.add_column("#", justify="right", style="dim")
    table.add_column("question")
    table.add_column(f"recall@{k}", justify="right")
    table.add_column(f"hit@{k}", justify="right")
    table.add_column("RR", justify="right")
    for q in report["per_query"]:
        miss = q["hit"] == 0.0
        table.add_row(
            str(q["id"]),
            (q["question"][:54] + "…") if len(q["question"]) > 55 else q["question"],
            f"{q['recall']:.2f}",
            "[red]0[/]" if miss else "1",
            f"{q['rr']:.2f}",
        )
    console.print(table)
    console.print(
        f"\n[bold]Recall@{k} = {report['recall']:.3f}[/]   "
        f"Hit@{k} = {report['hit']:.3f}   "
        f"MRR = {report['mrr']:.3f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Baseline retrieval metrics over the golden set.")
    parser.add_argument("--k", type=int, default=10, help="Cutoff for Recall@k / Hit@k.")
    args = parser.parse_args()

    console = Console()
    report = evaluate(k=args.k)
    _print(report, console)


if __name__ == "__main__":
    main()
