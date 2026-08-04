"""The externally-callable toolset — cahier §9 / acceptance gate C6.

Ten tools, each a thin `StructuredTool` over a function that already exists and is
already tested elsewhere in the package. Wrapping rather than reimplementing is the
whole point: a tool the model can call and a function the graph calls directly must
not be able to drift apart, because only one of them is covered by the suite.

Two rules the wrappers exist to enforce:

**Every path goes through ``check_path``.** A tool that takes a filename from a model
is the shortest route to reading ``/etc/passwd``, so ``read_file`` and ``list_files``
resolve their argument and confine it to a root — the session worktree when there is
one, otherwise the indexed target repo. A refusal is a logged §8.5 event, not just an
exception, which is what makes the guarantee auditable rather than merely claimed —
and it is logged to *these* settings' database rather than the process default, or
``guardrail_events`` would read a file the refusals never reached.

**No tool returns a raw object.** Every result is a string or a plain dict, because
the caller may be a model and a model cannot be handed a `SearchHit`.

Sandbox execution is *not* re-wrapped here — ``sandbox.tools.make_sandbox_tools``
already binds those three to one worktree, and rebinding them loosely would undo the
binding that stops a model pointing the sandbox at an arbitrary directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from forge.config import Settings, get_settings
from forge.core.workspace import Workspace
from forge.guardrails.events import get_log
from forge.guardrails.policy import check_path
from forge.rag.retrieve import Filters, hybrid_search
from forge.tools.ast_symbols import find_definitions, find_references
from forge.tools.ripgrep import ripgrep_search

_MAX_BYTES = 60_000
"""A file read is context, not a download. Bigger than any chunk, smaller than a prompt."""


# --- argument schemas ------------------------------------------------------


class SearchCodeArgs(BaseModel):
    query: str = Field(description="Natural-language question or a symbol name.")
    k: int = Field(default=8, description="How many chunks to return.")
    language: str | None = Field(default=None, description="Restrict to one language.")


class GrepArgs(BaseModel):
    pattern: str = Field(description="Literal text to find, unless regex=True.")
    regex: bool = Field(default=False, description="Treat the pattern as a regex.")
    word: bool = Field(default=False, description="Match whole words only.")
    max_count: int = Field(default=50, description="Cap on matching lines.")


class SymbolArgs(BaseModel):
    symbol: str = Field(description="The identifier to look up.")
    limit: int = Field(default=50, description="Cap on results.")


class ReadFileArgs(BaseModel):
    path: str = Field(description="Path relative to the repo root.")
    start_line: int = Field(default=1, description="1-indexed first line.")
    end_line: int = Field(default=0, description="Last line; 0 means to the end.")


class ListFilesArgs(BaseModel):
    subdir: str = Field(default="", description="Directory relative to the repo root.")
    pattern: str = Field(default="*", description="Glob, e.g. '*.py'.")
    limit: int = Field(default=200, description="Cap on paths returned.")


class GuardrailArgs(BaseModel):
    session_id: str = Field(default="", description="Restrict to one session.")
    limit: int = Field(default=50, description="Cap on events, newest first.")


# --- implementations -------------------------------------------------------


def _root_for(workspace: Workspace | None, settings: Settings) -> Path:
    """Where file tools are allowed to look: the worktree if there is one."""
    return Path(workspace.path if workspace is not None else settings.target_repo).resolve()


def _search_code(query: str, k: int = 8, language: str | None = None, **kw: Any) -> str:
    settings = kw.pop("_settings")
    hits = hybrid_search(query, k=k, filters=Filters(language=language), settings=settings, **kw)
    if not hits:
        return "no matches"
    return "\n".join(
        f"{h.chunk.path}:{h.chunk.start_line}-{h.chunk.end_line} "
        f"({h.chunk.symbol or h.chunk.kind.value}) via {h.provenance}"
        for h in hits
    )


def _grep(root: Path, pattern: str, regex: bool, word: bool, max_count: int) -> str:
    hits = ripgrep_search(pattern, root, fixed_string=not regex, word=word, max_count=max_count)
    if not hits:
        return "no matches"
    return "\n".join(f"{h.path}:{h.line}: {h.text.strip()}" for h in hits)


def _symbols(fn, symbol: str, root: Path, limit: int) -> str:
    hits = fn(symbol, root, limit=limit)
    if not hits:
        return f"no {'definition' if fn is find_definitions else 'reference'} of {symbol!r}"
    return "\n".join(f"{h.path}:{h.line} {h.kind} {h.name}" for h in hits)


def _read_file(
    root: Path, path: str, start_line: int, end_line: int, session_id: str, settings: Settings
) -> str:
    target = (root / path).resolve()
    decision = check_path(target, root, session_id=session_id, write=False, log=get_log(settings))
    if not decision:
        return f"refused: {decision.detail}"
    if not target.is_file():
        return f"not a file: {path}"
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        return f"unreadable: {error}"
    lines = text.splitlines()
    lo = max(1, start_line) - 1
    hi = len(lines) if end_line <= 0 else min(end_line, len(lines))
    body = "\n".join(f"{i + 1}\t{lines[i]}" for i in range(lo, hi))
    return body[:_MAX_BYTES] or "(empty range)"


def _list_files(
    root: Path, subdir: str, pattern: str, limit: int, session_id: str, settings: Settings
) -> str:
    base = (root / subdir).resolve() if subdir else root
    decision = check_path(base, root, session_id=session_id, write=False, log=get_log(settings))
    if not decision:
        return f"refused: {decision.detail}"
    if not base.is_dir():
        return f"not a directory: {subdir or '.'}"
    found = sorted(
        str(p.relative_to(root))
        for p in base.rglob(pattern)
        if p.is_file() and ".git" not in p.parts
    )
    return "\n".join(found[:limit]) or "no files"


def _guardrail_events(session_id: str, limit: int, settings: Settings) -> str:
    events = get_log(settings).events(session_id=session_id or None, limit=limit)
    if not events:
        return "no guardrail events"
    return "\n".join(
        f"[{e.action.value}] {e.rule} ({e.stage.value}) {e.target} — {e.detail}" for e in events
    )


# --- the registry ----------------------------------------------------------


def build_toolset(
    *,
    settings: Settings | None = None,
    workspace: Workspace | None = None,
    client: Any = None,
    embedder: Any = None,
    encoder: Any = None,
    session_id: str = "",
    include_sandbox: bool = True,
) -> list[BaseTool]:
    """The ten tools (cahier §9, C6).

    ``workspace`` decides two things: which root the file tools are confined to, and
    whether the three sandbox tools are included at all — they cannot exist without a
    worktree to run in. Pass ``include_sandbox=False`` to list the knowledge tools
    alone, which is what ``forge tools`` does outside a session.
    """
    settings = settings or get_settings()
    root = _root_for(workspace, settings)
    search_kw = {"client": client, "embedder": embedder, "encoder": encoder, "_settings": settings}

    tools: list[BaseTool] = [
        StructuredTool.from_function(
            func=lambda query, k=8, language=None: _search_code(query, k, language, **search_kw),
            name="search_code",
            description=(
                "Hybrid semantic + lexical search over the indexed repository. The general "
                "way to find code by meaning; returns path:line spans with provenance."
            ),
            args_schema=SearchCodeArgs,
        ),
        StructuredTool.from_function(
            func=lambda pattern, regex=False, word=False, max_count=50: _grep(
                root, pattern, regex, word, max_count
            ),
            name="ripgrep_search",
            description=(
                "Exact text or regex search over the working tree, line by line. Use when "
                "you know the literal string; search_code is better for concepts."
            ),
            args_schema=GrepArgs,
        ),
        StructuredTool.from_function(
            func=lambda symbol, limit=50: _symbols(find_definitions, symbol, root, limit),
            name="find_definitions",
            description="Locate where a symbol is defined, using the Python AST, not text.",
            args_schema=SymbolArgs,
        ),
        StructuredTool.from_function(
            func=lambda symbol, limit=50: _symbols(find_references, symbol, root, limit),
            name="find_references",
            description="Locate every call or reference to a symbol, using the Python AST.",
            args_schema=SymbolArgs,
        ),
        StructuredTool.from_function(
            func=lambda path, start_line=1, end_line=0: _read_file(
                root, path, start_line, end_line, session_id, settings
            ),
            name="read_file",
            description=(
                "Read a numbered line range from one file, confined to the repository root. "
                "Paths that resolve outside it are refused and logged."
            ),
            args_schema=ReadFileArgs,
        ),
        StructuredTool.from_function(
            func=lambda subdir="", pattern="*", limit=200: _list_files(
                root, subdir, pattern, limit, session_id, settings
            ),
            name="list_files",
            description="List files under the repository root, optionally filtered by a glob.",
            args_schema=ListFilesArgs,
        ),
        StructuredTool.from_function(
            func=lambda session_id="", limit=50: _guardrail_events(session_id, limit, settings),
            name="guardrail_events",
            description=(
                "Query the §8.5 guardrail log — every input, injection, policy and output "
                "decision, newest first, with the rule id and the action taken."
            ),
            args_schema=GuardrailArgs,
        ),
    ]

    if include_sandbox and workspace is not None:
        from forge.sandbox.tools import make_sandbox_tools

        tools.extend(make_sandbox_tools(workspace, settings=settings))

    return tools


def describe_toolset(tools: list[BaseTool]) -> list[dict[str, str]]:
    """Name/description/args triples — what ``forge tools`` prints."""
    rows = []
    for tool in tools:
        schema = getattr(tool, "args_schema", None)
        args = ", ".join((schema.model_fields if schema else {}).keys())
        rows.append(
            {
                "name": tool.name,
                "args": args,
                "description": " ".join((tool.description or "").split()),
            }
        )
    return rows


__all__ = ["build_toolset", "describe_toolset"]
