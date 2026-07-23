"""Literal search over the target repo — cahier §4 (RETRIEVER), §6.5.

ripgrep, not a Python re-implementation of it: it is faster, it already respects
`.gitignore`, and it is what makes *"find every call site of parse_config"* exact
where a dense embedding is only approximate. A deterministic tool — same input,
same lines, no model in the loop — which is why the query gate in `retrieve.py`
sends bare identifiers straight here instead of paying an LLM to rewrite them.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_RG = shutil.which("rg")


@dataclass(frozen=True)
class RipgrepHit:
    """One matching line. ``path`` is repo-relative POSIX, matching a chunk's."""

    path: str
    line: int
    """1-indexed, matching what an editor and a citation show."""
    text: str


def ripgrep_available() -> bool:
    """False on a machine without ripgrep, so callers can degrade rather than crash."""
    return _RG is not None


def ripgrep_search(
    pattern: str,
    repo: str | Path,
    *,
    fixed_string: bool = True,
    word: bool = False,
    ignore_case: bool = False,
    globs: list[str] | None = None,
    max_count: int = 200,
    timeout_s: int = 30,
) -> list[RipgrepHit]:
    """Every line under ``repo`` matching ``pattern``.

    ``fixed_string`` treats the pattern literally (the right default for a symbol);
    ``word`` adds word boundaries so ``config`` does not match ``configuration``.
    The two compose — a whole-word literal — which is what an identifier lookup
    wants. Results are capped at ``max_count`` because a common token can match
    thousands of lines and only the first screenful ever reaches a ranking.
    """
    if _RG is None:
        raise RuntimeError("ripgrep (rg) is not installed")
    repo = Path(repo)

    cmd = [_RG, "--json", "--line-number", "--no-heading"]
    if fixed_string:
        cmd.append("--fixed-strings")
    if word:
        cmd.append("--word-regexp")
    if ignore_case:
        cmd.append("--ignore-case")
    for glob in globs or []:
        cmd += ["--glob", glob]
    cmd += ["--", pattern, "."]

    proc = subprocess.run(
        cmd, cwd=str(repo), capture_output=True, text=True, timeout=timeout_s, check=False
    )
    # rg exits 1 on "no matches", which is a valid empty result, not a failure.
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"ripgrep failed ({proc.returncode}): {proc.stderr.strip()[:200]}")

    hits: list[RipgrepHit] = []
    for raw in proc.stdout.splitlines():
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue
        data = event["data"]
        # `text` is absent when the path/line is not valid UTF-8; skip those
        # rather than guess an encoding — they are never source we indexed.
        path = (data.get("path") or {}).get("text")
        line_text = (data.get("lines") or {}).get("text")
        if path is None or line_text is None:
            continue
        hits.append(
            RipgrepHit(
                path=path.lstrip("./"),
                line=data["line_number"],
                text=line_text.rstrip("\n").strip(),
            )
        )
        if len(hits) >= max_count:
            break
    return hits
