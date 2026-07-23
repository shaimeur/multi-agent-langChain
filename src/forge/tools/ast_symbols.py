"""Tree-sitter symbol lookup — cahier §4 (RETRIEVER).

Where ripgrep matches *text*, this matches *structure*: the ``def``/``class``
node named ``refresh``, not every line that mentions the word — and, for
references, the identifier uses that the parser recognises, so occurrences inside
strings and comments do not count. That distinction between a definition and a
reference is precisely what the Planner needs when it asks "where is this
actually defined", and it is not something a substring search can answer.

Deterministic, same parser the chunker uses (`tree_sitter_python`), no model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

from forge.rag.walker import walk_repo

PY_LANGUAGE = Language(tspython.language())
_DEF_TYPES = {"function_definition", "class_definition"}


@dataclass(frozen=True)
class SymbolHit:
    """A definition or reference site. ``path`` is repo-relative POSIX."""

    path: str
    line: int
    """1-indexed definition/use line."""
    end_line: int
    kind: str
    """``function`` | ``class`` for a definition, ``reference`` for a use."""
    name: str
    signature: str
    """The source line, stripped — enough to read the hit without opening it."""


def _parser() -> Parser:
    return Parser(PY_LANGUAGE)


def _walk(root: Node):
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(node.children)


def _name(node: Node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _enclosing_class(node: Node, src: bytes) -> str | None:
    """Name of the nearest ancestor ``class``, or None — used to resolve a
    dotted ``ClassName.method`` to the method on *that* class, not a same-named
    method elsewhere."""
    parent = node.parent
    while parent is not None:
        if parent.type == "class_definition":
            name = parent.child_by_field_name("name")
            return _name(name, src) if name is not None else None
        parent = parent.parent
    return None


def _python_sources(repo: Path):
    for source in walk_repo(repo):
        if source.language == "python":
            yield source


def find_definitions(symbol: str, repo: str | Path, *, limit: int = 50) -> list[SymbolHit]:
    """Every ``def``/``class`` that defines ``symbol``.

    ``symbol`` may be a bare name (``refresh``) or dotted (``Cursor.refresh``);
    the dotted form additionally requires the enclosing class to match, which is
    how two classes with a same-named method stay distinguishable.
    """
    *prefix, leaf = symbol.split(".")
    want_class = prefix[-1] if prefix else None
    parser = _parser()

    hits: list[SymbolHit] = []
    for source in _python_sources(Path(repo)):
        src = source.text.encode("utf-8")
        lines = source.text.splitlines()
        for node in _walk(parser.parse(src).root_node):
            if node.type not in _DEF_TYPES:
                continue
            name_node = node.child_by_field_name("name")
            if name_node is None or _name(name_node, src) != leaf:
                continue
            if want_class is not None and _enclosing_class(node, src) != want_class:
                continue
            start = node.start_point[0]
            hits.append(
                SymbolHit(
                    path=source.rel_path,
                    line=start + 1,
                    end_line=node.end_point[0] + 1,
                    kind="class" if node.type == "class_definition" else "function",
                    name=leaf,
                    signature=lines[start].strip() if start < len(lines) else "",
                )
            )
            if len(hits) >= limit:
                return hits
    return hits


def find_references(symbol: str, repo: str | Path, *, limit: int = 200) -> list[SymbolHit]:
    """Every identifier *use* of ``symbol`` the parser recognises.

    Matches the leaf name only and skips string/comment text — the structural
    difference from ``ripgrep_search`` that makes this the right tool for "who
    calls this" rather than "what mentions this".
    """
    leaf = symbol.split(".")[-1]
    parser = _parser()

    hits: list[SymbolHit] = []
    seen: set[tuple[str, int]] = set()
    for source in _python_sources(Path(repo)):
        src = source.text.encode("utf-8")
        lines = source.text.splitlines()
        for node in _walk(parser.parse(src).root_node):
            if node.type != "identifier" or _name(node, src) != leaf:
                continue
            line = node.start_point[0]
            key = (source.rel_path, line + 1)
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                SymbolHit(
                    path=source.rel_path,
                    line=line + 1,
                    end_line=line + 1,
                    kind="reference",
                    name=leaf,
                    signature=lines[line].strip() if line < len(lines) else "",
                )
            )
            if len(hits) >= limit:
                return hits
    return hits
