"""AST chunking is the RAG differentiator (cahier §6.2), so it gets asserted hard."""

from __future__ import annotations

from forge.models import ChunkKind
from forge.rag.chunkers import MAX_CHUNK_CHARS, chunk_file
from forge.rag.walker import SourceFile

SAMPLE = '''\
"""Module docstring."""

import jwt
from redis import Redis

TIMEOUT = 30


def parse_config(path):
    """Read config from disk."""
    return {}


class SessionManager:
    """Validates and refreshes user sessions."""

    def __init__(self, store):
        self.store = store

    @property
    def active(self):
        """Live sessions."""
        return self.store.count()
'''


def _src(text: str, rel_path: str = "src/auth/session.py", language: str = "python"):
    return SourceFile(path=None, rel_path=rel_path, language=language, text=text)  # type: ignore[arg-type]


def _chunks(text: str = SAMPLE, **kw):
    return chunk_file(_src(text, **kw), repo="demo")


def _by_symbol(chunks, symbol):
    return next(c for c in chunks if c.symbol == symbol)


def test_splits_at_syntax_boundaries_not_character_counts():
    symbols = {c.symbol for c in _chunks()}

    assert "parse_config" in symbols
    assert "SessionManager" in symbols
    assert "SessionManager.__init__" in symbols
    assert "SessionManager.active" in symbols


def test_methods_point_at_their_class_as_parent():
    chunks = _chunks()
    cls = _by_symbol(chunks, "SessionManager")
    method = _by_symbol(chunks, "SessionManager.__init__")

    assert method.parent_id == cls.chunk_id
    assert method.kind is ChunkKind.METHOD
    assert cls.kind is ChunkKind.CLASS


def test_line_spans_resolve_to_the_real_source():
    """A citation that points at the wrong lines is worse than no citation."""
    lines = SAMPLE.splitlines()
    for chunk in _chunks():
        assert 1 <= chunk.start_line <= chunk.end_line <= len(lines)

    fn = _by_symbol(_chunks(), "parse_config")
    assert lines[fn.start_line - 1].startswith("def parse_config")


def test_decorator_is_inside_the_span():
    """Citing the `def` line of a decorated method hides what decorates it."""
    active = _by_symbol(_chunks(), "SessionManager.active")

    assert SAMPLE.splitlines()[active.start_line - 1].strip() == "@property"
    assert "@property" in active.raw


def test_embedded_text_carries_the_enrichment_header():
    method = _by_symbol(_chunks(), "SessionManager.__init__")

    assert "# file: src/auth/session.py" in method.text
    assert "# class: SessionManager" in method.text
    assert "jwt" in method.text and "redis" in method.text


def test_raw_stays_clean_for_display():
    """`raw` is what a human reads in a diff — the header would be noise there."""
    method = _by_symbol(_chunks(), "SessionManager.__init__")

    assert not method.raw.startswith("# file:")
    assert method.raw.lstrip().startswith("def __init__")


def test_docstring_is_lifted_into_the_header():
    assert "# docstring: Read config from disk." in _by_symbol(_chunks(), "parse_config").text


def test_module_level_code_is_not_lost():
    """Imports and constants belong to no definition but still answer questions."""
    module = next(c for c in _chunks() if c.kind is ChunkKind.MODULE)

    assert "TIMEOUT = 30" in module.raw
    assert "import jwt" in module.raw


def test_chunk_ids_are_stable_across_reindexing():
    """Editing a body must update a chunk in place, not orphan it."""
    edited = SAMPLE.replace("return {}", "return {'a': 1}")

    before = _by_symbol(_chunks(), "parse_config")
    after = _by_symbol(_chunks(edited), "parse_config")

    assert before.chunk_id == after.chunk_id
    assert before.raw != after.raw


def test_oversized_definitions_are_split_with_honest_line_numbers():
    body = "\n".join(f"    x{i} = {i}" for i in range(MAX_CHUNK_CHARS // 8))
    chunks = _chunks(f"def huge():\n{body}\n")

    assert len(chunks) > 1
    assert all(len(c.raw) <= MAX_CHUNK_CHARS * 1.2 for c in chunks)
    assert [c.start_line for c in chunks] == sorted(c.start_line for c in chunks)


def test_syntactically_broken_python_still_indexes():
    """FORGE gets pointed at repos precisely because something in them is wrong."""
    chunks = _chunks("def broken(:\n    ???\n")

    assert chunks, "a parse failure must not silently drop the file"


def test_markdown_splits_on_headings():
    doc = "# Title\n\nintro\n\n## Setup\n\nrun it\n\n## Usage\n\ncall it\n"
    chunks = chunk_file(_src(doc, rel_path="README.md", language="markdown"), repo="demo")

    assert [c.symbol for c in chunks] == ["Title", "Setup", "Usage"]
    assert all(c.kind is ChunkKind.PROSE for c in chunks)


def test_citation_format_is_clickable():
    assert _by_symbol(_chunks(), "parse_config").citation.startswith("src/auth/session.py:")
