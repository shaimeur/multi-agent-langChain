"""AST-aware chunking — cahier §6.2.

Splitting code every N characters cuts functions in half and strands bodies from
the signatures that explain them. Splitting at syntax boundaries instead means a
chunk is a thing a developer would recognise: one function, one method, one class.

Each chunk is embedded with a header giving it the context its own text lacks —
the file it lives in, the class enclosing it, what the module imports, its
docstring. The header is embedded but never shown in a diff, which is why
``Chunk`` carries ``text`` and ``raw`` separately.
"""

from __future__ import annotations

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

from forge.models import Chunk, ChunkKind, make_chunk_id
from forge.rag.walker import SourceFile

PY_LANGUAGE = Language(tspython.language())

MAX_CHUNK_CHARS = 6_000
"""Above this a single definition is split by lines — a 500-line function is
real, and a chunk that large would swamp the token budget of the pack."""

_DEF_NODES = frozenset({"function_definition", "class_definition", "decorated_definition"})


def _parser() -> Parser:
    return Parser(PY_LANGUAGE)


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _definition(node: Node) -> Node:
    """Unwrap ``decorated_definition`` to the def it decorates."""
    if node.type != "decorated_definition":
        return node
    for child in node.children:
        if child.type in ("function_definition", "class_definition"):
            return child
    return node


def _name_of(node: Node, src: bytes) -> str | None:
    name = _definition(node).child_by_field_name("name")
    return _text(name, src) if name is not None else None


def _docstring(node: Node, src: bytes) -> str | None:
    """First string literal in the body, collapsed to a single line."""
    body = _definition(node).child_by_field_name("body")
    if body is None or not body.children:
        return None
    first = body.children[0]
    if first.type != "expression_statement" or not first.children:
        return None
    literal = first.children[0]
    if literal.type != "string":
        return None
    raw = _text(literal, src).strip("\"'\n ")
    line = " ".join(raw.split())
    return line[:200] or None


def _imports(root: Node, src: bytes) -> list[str]:
    """Module names imported at any level, deduplicated, in source order."""
    found: list[str] = []
    stack = list(root.children)
    while stack:
        node = stack.pop(0)
        if node.type in ("import_statement", "import_from_statement"):
            module = node.child_by_field_name("module_name")
            target = module or node
            for part in _text(target, src).replace(",", " ").split():
                if part in ("import", "from", "as"):
                    continue
                head = part.split(".")[0].strip("()")
                if head and head not in found:
                    found.append(head)
        elif node.type in ("block", "if_statement", "try_statement"):
            stack.extend(node.children)
    return found[:12]


def _header(
    *,
    path: str,
    language: str,
    symbol: str | None,
    cls: str | None,
    imports: list[str],
    docstring: str | None,
) -> str:
    lines = [f"# file: {path}  | lang: {language}"]
    if cls:
        lines.append(f"# class: {cls}")
    if symbol:
        lines.append(f"# symbol: {symbol}")
    if imports:
        lines.append(f"# imports: {', '.join(imports)}")
    if docstring:
        lines.append(f"# docstring: {docstring}")
    return "\n".join(lines)


def _split_oversized(raw: str, start_line: int) -> list[tuple[str, int, int]]:
    """Break a too-large unit into line windows, preserving true line numbers."""
    lines = raw.splitlines()
    if len(raw) <= MAX_CHUNK_CHARS:
        return [(raw, start_line, start_line + max(len(lines) - 1, 0))]

    # Roughly MAX_CHUNK_CHARS per window, measured in lines to keep spans honest.
    avg = max(len(raw) // max(len(lines), 1), 1)
    window = max(MAX_CHUNK_CHARS // avg, 20)
    out: list[tuple[str, int, int]] = []
    for i in range(0, len(lines), window):
        block = lines[i : i + window]
        out.append(("\n".join(block), start_line + i, start_line + i + len(block) - 1))
    return out


def _emit(
    *,
    repo: str,
    source: SourceFile,
    git_sha: str,
    kind: ChunkKind,
    symbol: str | None,
    raw: str,
    start_line: int,
    parent_id: str | None,
    cls: str | None,
    imports: list[str],
    docstring: str | None,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    for body, first, last in _split_oversized(raw, start_line):
        header = _header(
            path=source.rel_path,
            language=source.language,
            symbol=symbol,
            cls=cls,
            imports=imports,
            docstring=docstring,
        )
        chunks.append(
            Chunk(
                chunk_id=make_chunk_id(repo, source.rel_path, symbol, first),
                repo=repo,
                path=source.rel_path,
                language=source.language,
                kind=kind,
                symbol=symbol,
                start_line=first,
                end_line=last,
                git_sha=git_sha,
                parent_id=parent_id,
                text=f"{header}\n{body}",
                raw=body,
            )
        )
    return chunks


def chunk_python(source: SourceFile, repo: str, git_sha: str) -> list[Chunk]:
    """One chunk per function, method and class, plus module-level leftovers."""
    src = source.text.encode("utf-8")
    tree = _parser().parse(src)
    root = tree.root_node
    imports = _imports(root, src)

    chunks: list[Chunk] = []
    covered: set[int] = set()

    for node in root.children:
        if node.type not in _DEF_NODES:
            continue
        inner = _definition(node)
        name = _name_of(node, src)
        # Span starts at the decorator, not the `def` it decorates.
        start_line = node.start_point[0] + 1
        covered.update(range(node.start_point[0], node.end_point[0] + 1))

        if inner.type == "class_definition":
            class_chunks = _emit(
                repo=repo,
                source=source,
                git_sha=git_sha,
                kind=ChunkKind.CLASS,
                symbol=name,
                raw=_text(node, src),
                start_line=start_line,
                parent_id=None,
                cls=None,
                imports=imports,
                docstring=_docstring(node, src),
            )
            chunks.extend(class_chunks)
            parent_id = class_chunks[0].chunk_id if class_chunks else None

            body = inner.child_by_field_name("body")
            for member in body.children if body else []:
                if member.type not in _DEF_NODES:
                    continue
                if _definition(member).type != "function_definition":
                    continue
                method = _name_of(member, src)
                chunks.extend(
                    _emit(
                        repo=repo,
                        source=source,
                        git_sha=git_sha,
                        kind=ChunkKind.METHOD,
                        symbol=f"{name}.{method}" if name and method else method,
                        raw=_text(member, src),
                        start_line=member.start_point[0] + 1,
                        parent_id=parent_id,
                        cls=name,
                        imports=imports,
                        docstring=_docstring(member, src),
                    )
                )
        else:
            chunks.extend(
                _emit(
                    repo=repo,
                    source=source,
                    git_sha=git_sha,
                    kind=ChunkKind.FUNCTION,
                    symbol=name,
                    raw=_text(node, src),
                    start_line=start_line,
                    parent_id=None,
                    cls=None,
                    imports=imports,
                    docstring=_docstring(node, src),
                )
            )

    module_lines = [
        (i, line)
        for i, line in enumerate(source.text.splitlines())
        if i not in covered and line.strip()
    ]
    if module_lines:
        chunks.extend(
            _emit(
                repo=repo,
                source=source,
                git_sha=git_sha,
                kind=ChunkKind.MODULE,
                symbol=None,
                raw="\n".join(line for _, line in module_lines),
                start_line=module_lines[0][0] + 1,
                parent_id=None,
                cls=None,
                imports=imports,
                docstring=None,
            )
        )
    return chunks


def chunk_prose(source: SourceFile, repo: str, git_sha: str) -> list[Chunk]:
    """Split markdown on headings; anything else stays whole.

    Headings are the document's own structure, so they are the honest analogue of
    a syntax boundary. Config files are small enough to keep intact.
    """
    lines = source.text.splitlines()
    sections: list[tuple[str, int]] = []
    current: list[str] = []
    start = 1
    heading: str | None = None
    symbols: list[str | None] = []

    for i, line in enumerate(lines, start=1):
        if source.language == "markdown" and line.startswith("#"):
            if current:
                sections.append(("\n".join(current), start))
                symbols.append(heading)
            current, start, heading = [line], i, line.lstrip("# ").strip() or None
        else:
            current.append(line)
    if current:
        sections.append(("\n".join(current), start))
        symbols.append(heading)

    chunks: list[Chunk] = []
    for (body, first), symbol in zip(sections, symbols, strict=True):
        if not body.strip():
            continue
        chunks.extend(
            _emit(
                repo=repo,
                source=source,
                git_sha=git_sha,
                kind=ChunkKind.PROSE,
                symbol=symbol,
                raw=body,
                start_line=first,
                parent_id=None,
                cls=None,
                imports=[],
                docstring=None,
            )
        )
    return chunks


def chunk_file(source: SourceFile, repo: str, git_sha: str = "") -> list[Chunk]:
    """Dispatch on language. Anything without a syntax chunker falls back to prose."""
    if source.language == "python":
        try:
            return chunk_python(source, repo, git_sha)
        except (RecursionError, ValueError, UnicodeDecodeError):
            # A syntactically broken file is still worth indexing — FORGE is
            # often pointed at a repo precisely because something is wrong in it.
            return chunk_prose(source, repo, git_sha)
    return chunk_prose(source, repo, git_sha)
