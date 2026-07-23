"""Deterministic retrieval tools — ripgrep literal search and AST symbol lookup.

No index, no model: these read the repo on disk. The point of each assertion is
the property that separates a structural tool from a substring grep — word
boundaries, dotted-name resolution, and identifiers-not-strings.
"""

from __future__ import annotations

import subprocess

import pytest

from forge.tools.ast_symbols import find_definitions, find_references
from forge.tools.ripgrep import ripgrep_available, ripgrep_search

pytestmark = pytest.mark.skipif(not ripgrep_available(), reason="ripgrep (rg) not installed")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "tokenizer.py").write_text(
        '"""Tokenizer. See tokenize for details."""\n'  # 'tokenize' in a string
        "\n"
        "def tokenize(text):\n"
        '    """Split raw SQL into tokens."""\n'
        "    return text.split()\n"
        "\n"
        "\n"
        "class Lexer:\n"
        "    def scan(self, sql):\n"
        "        # calls tokenize below\n"  # 'tokenize' in a comment
        "        return tokenize(sql)\n"
    )
    (root / "src" / "other.py").write_text(
        "def scan(data):\n    return data\n"  # a second scan, module-level
    )
    (root / "src" / "settings.py").write_text(
        "config = 1\nconfiguration = 2\n"  # word-boundary bait
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    return root


def test_ripgrep_finds_the_literal(repo):
    hits = ripgrep_search("tokenize", repo)
    lines = {(h.path, h.line) for h in hits}

    # The definition, the reference, the comment and the docstring all mention it.
    assert ("src/tokenizer.py", 3) in lines  # def tokenize
    assert ("src/tokenizer.py", 11) in lines  # return tokenize(sql)


def test_ripgrep_word_boundary_excludes_substrings(repo):
    hits = ripgrep_search("config", repo, word=True)
    matched = {h.text for h in hits}

    assert "config = 1" in matched
    assert "configuration = 2" not in matched, "a whole-word search must not match a substring"


def test_ripgrep_no_match_is_empty_not_an_error(repo):
    assert ripgrep_search("definitely_not_present_anywhere", repo) == []


def test_find_definitions_locates_a_function(repo):
    hits = find_definitions("tokenize", repo)

    assert len(hits) == 1
    assert hits[0].path == "src/tokenizer.py"
    assert hits[0].kind == "function"
    assert hits[0].line == 3


def test_find_definitions_dotted_name_resolves_the_class(repo):
    """`Lexer.scan` is the method on Lexer, not the module-level scan."""
    scoped = find_definitions("Lexer.scan", repo)
    assert len(scoped) == 1
    assert scoped[0].path == "src/tokenizer.py"

    both = find_definitions("scan", repo)
    assert {h.path for h in both} == {"src/tokenizer.py", "src/other.py"}


def test_find_references_are_identifiers_not_strings(repo):
    """The docstring and comment mention 'tokenize'; neither is a reference."""
    refs = find_references("tokenize", repo)
    lines = {h.line for h in refs}

    assert 11 in lines, "the call site is a reference"
    assert 1 not in lines, "the module docstring is not"
    assert 10 not in lines, "the comment is not"
