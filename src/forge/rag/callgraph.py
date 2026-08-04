"""One-hop callee expansion — the fix for `docs/limitations.md` §8 (O7).

Retrieval scores chunks against the *words of the question*, and has no notion of
"and whatever that function calls". When the symbol naming the defect is one the
bug report never mentions, no amount of ``k`` finds it.

`swe_mini`'s SM-01 is the measured case. The report says *"asking the identifier
for its real name returns the name with a trailing double quote"*; the defect is in
``sqlparse.utils.remove_quotes``, which the report never names. At the live
``retrieval_top_k`` of 8 that chunk is **not retrieved** — but rank 6,
``TokenList._get_first_name``, calls it directly. So the fix site is one hop from
something retrieval already found, and this module walks that hop.

**Extraction is structural, resolution is metadata.** The callees come from the
same tree-sitter parser the chunker and ``tools/ast_symbols.py`` use, so a name
inside a string or a comment is not a call. Resolving them, though, goes through
the indexed chunks rather than through ``find_definitions`` as §8 originally
scoped it. Two reasons, both practical: ``find_definitions`` re-parses every file
in the repo *per symbol* and then still has to map a definition line back to a
chunk — and only a chunk that exists in the index can be added to a pack at all,
so the index is the authoritative set of resolvable targets anyway.

**A callee is added alone, not with its parent document.** Parent expansion earns
its budget on the chunk that actually matched the query; a callee is a targeted
answer to "what does this call", and pulling in its whole enclosing class as well
would spend the token budget faster than the extra context repays.

Off by default (``RETRIEVAL_EXPAND_CALLS``), for the same reason the cross-encoder
reranker is off: it changes the pack for every query, and the pack is embedded in
the prompt that keys the replay fixtures. See `docs/limitations.md` §8 for the
measurement and `docs/evaluation.md` for what it does to the ablation.
"""

from __future__ import annotations

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

from forge.models import Chunk, ChunkKind

PY_LANGUAGE = Language(tspython.language())

MAX_CALLEES_PER_CHUNK = 3
"""Cap per matched chunk. A class body calls dozens of things; the budget is finite
and an unranked flood of them would push out the evidence that actually matched."""

_DEFINING_KINDS = {ChunkKind.FUNCTION, ChunkKind.METHOD, ChunkKind.CLASS}

_NOISE = """
    abs aiter all anext any ascii bin bool breakpoint bytearray bytes callable chr
    classmethod compile complex delattr dict dir divmod enumerate eval exec filter
    float format frozenset getattr globals hasattr hash help hex id input int
    isinstance issubclass iter len list locals map max memoryview min next object
    oct open ord pow print property range repr reversed round set setattr slice
    sorted staticmethod str sum super tuple type vars zip
    append add extend insert remove pop keys values items get update join split
    strip lstrip rstrip startswith endswith replace lower upper encode decode
"""
"""Builtins and the ubiquitous stdlib methods. Resolving ``str.split`` against the
target repo either finds nothing or finds a same-named method that has nothing to do
with the call — noise either way, and it spends the per-chunk cap.

Written as prose and split rather than as ninety quoted strings: this is a list a
human has to read and extend, and one word per line would bury it."""

_BUILTINS = frozenset(_NOISE.split())


def _walk(root: Node):
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(node.children)


def callees(source: str, *, limit: int = MAX_CALLEES_PER_CHUNK) -> list[str]:
    """The names ``source`` calls, in first-appearance order.

    Structural, not textual: ``remove_quotes`` written in a docstring is not a call.
    A method call contributes its leaf (``self._get_first_name`` → ``_get_first_name``)
    because that is what a definition is indexed under.

    Chunks are fragments — an indented method body is not a valid module — but
    tree-sitter is error-tolerant and recovers the call nodes inside one anyway,
    which is why this uses the parser rather than ``ast``.
    """
    src = source.encode("utf-8")
    tree = Parser(PY_LANGUAGE).parse(src)

    found: list[str] = []
    seen: set[str] = set()
    # Post-order by position: `_walk` is a stack, so sort back into source order to
    # make "first appearance" mean what it says and the result deterministic.
    calls = sorted(
        (n for n in _walk(tree.root_node) if n.type == "call"), key=lambda n: n.start_byte
    )
    for node in calls:
        function = node.child_by_field_name("function")
        if function is None:
            continue
        if function.type == "identifier":
            name_node = function
        elif function.type == "attribute":
            name_node = function.child_by_field_name("attribute")
        else:
            continue  # a call on a subscript or a call result — no name to resolve
        if name_node is None:
            continue
        name = src[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")
        if name in seen or name in _BUILTINS or name.startswith("__"):
            continue
        seen.add(name)
        found.append(name)
        if len(found) >= limit:
            break
    return found


def build_symbol_index(groups: dict[tuple[str, str], list[Chunk]]) -> dict[str, list[Chunk]]:
    """Leaf symbol name → the chunks that define it.

    Built from the same ``load_groups`` scroll the packer already performs, so the
    hop costs no extra query. Leaf-keyed because a call site says ``remove_quotes``,
    never ``utils.remove_quotes`` — and the ambiguity that creates is handled at
    lookup, where the caller's own file is known and can break the tie.
    """
    index: dict[str, list[Chunk]] = {}
    for members in groups.values():
        for chunk in members:
            if chunk.kind not in _DEFINING_KINDS or not chunk.symbol:
                continue
            index.setdefault(chunk.symbol.split(".")[-1], []).append(chunk)
    return index


def resolve_callees(
    chunk: Chunk, index: dict[str, list[Chunk]], *, limit: int = MAX_CALLEES_PER_CHUNK
) -> list[Chunk]:
    """The definition chunks for what ``chunk`` calls, excluding itself.

    Where a name is defined more than once, the definition in the caller's own file
    wins — ``get_real_name`` exists on three classes in ``sqlparse/sql.py``, and the
    one in the file being read is the likelier referent. Beyond that the ambiguity
    is not resolvable from a leaf name alone, so only the first candidate is taken:
    adding all of them would spend the budget on guesses.
    """
    out: list[Chunk] = []
    for name in callees(chunk.raw, limit=limit):
        candidates = [c for c in index.get(name, []) if c.chunk_id != chunk.chunk_id]
        if not candidates:
            continue
        same_file = [c for c in candidates if c.path == chunk.path]
        out.append((same_file or candidates)[0])
    return out


__all__ = ["MAX_CALLEES_PER_CHUNK", "build_symbol_index", "callees", "resolve_callees"]
