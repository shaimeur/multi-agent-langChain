"""Four seeded bugs in the pinned target repo — the D8 repair benchmark.

Four, not the cahier's ten: descope §7. The harness runs *N*, so the limit is free-tier
quota rather than anything in the code — raising it is adding entries to this list.

Each bug is a single search/replace that turns correct sqlparse into plausibly broken
sqlparse, paired with a **hidden** test that the agent never sees. That separation is
the whole point of the benchmark: the SANDBOX_ENGINEER writes its own regression test
from the bug report, and the hidden test is what decides whether the repair actually
worked. An agent that games its own test still fails here.

Every bug is deliberately of the kind a human really writes — an off-by-one, a `while`
that should not have become an `if`, a dropped guard clause — not a syntax error or a
deleted function. A benchmark made of obvious breakage measures nothing.

`old` must occur **exactly once** in its file; ``verify_bugs`` asserts that, so a bug
that stops applying after a target-repo bump fails loudly instead of silently passing.
"""

from __future__ import annotations

from dataclasses import dataclass

from forge.models import Patch, PatchSet


@dataclass(frozen=True)
class SeededBug:
    """One breakable behaviour: how to break it, and how to tell it is fixed."""

    bug_id: str
    title: str
    report: str
    """The change request a user would actually type. This is the agent's only input."""
    path: str
    correct: str
    """The real code, as pinned. Also what a perfect repair restores — but not the
    only acceptable answer: the hidden test grades behaviour, not text."""
    broken: str
    hidden_test_path: str
    hidden_test: str

    def break_patchset(self) -> PatchSet:
        """correct → broken. Applied to seed the bug into a session worktree."""
        return PatchSet(
            summary=f"seed {self.bug_id}",
            patches=[Patch(path=self.path, old_string=self.correct, new_string=self.broken)],
        )

    def repair_patchset(self) -> PatchSet:
        """broken → correct. The reference fix, used to prove the bug is *fixable*
        and that the hidden test really does go green — never given to the agent."""
        return PatchSet(
            summary=f"reference fix {self.bug_id}",
            patches=[Patch(path=self.path, old_string=self.broken, new_string=self.correct)],
        )


BUGS: list[SeededBug] = [
    SeededBug(
        bug_id="SM-01",
        title="remove_quotes leaves the closing quote behind",
        report=(
            "Quoted identifiers come back wrong: parsing 'select * from \"my table\"' and "
            "asking the identifier for its real name returns the name with a trailing "
            "double quote still attached. It should be just: my table"
        ),
        path="sqlparse/utils.py",
        correct="        val = val[1:-1]",
        broken="        val = val[1:]",
        hidden_test_path="tests/test_sm01_hidden.py",
        hidden_test=(
            "import sqlparse\n\n\n"
            "def test_quoted_identifier_loses_both_quotes():\n"
            "    identifier = sqlparse.parse('select * from \"my table\"')[0].tokens[-1]\n"
            "    assert identifier.get_real_name() == 'my table'\n"
        ),
    ),
    SeededBug(
        bug_id="SM-02",
        title="strip_semicolon only removes one trailing token",
        report=(
            "sqlparse.split('select 1; ', strip_semicolon=True) returns ['select 1;'] "
            "instead of ['select 1'] — the semicolon survives when there is trailing "
            "whitespace after it. With no trailing space it works."
        ),
        path="sqlparse/filters/others.py",
        correct="        while stmt.tokens and (stmt.tokens[-1].is_whitespace",
        broken="        if stmt.tokens and (stmt.tokens[-1].is_whitespace",
        hidden_test_path="tests/test_sm02_hidden.py",
        hidden_test=(
            "import sqlparse\n\n\n"
            "def test_trailing_semicolon_is_stripped_through_whitespace():\n"
            "    assert sqlparse.split('select 1; ', strip_semicolon=True) == ['select 1']\n"
            "    assert sqlparse.split('select 1;', strip_semicolon=True) == ['select 1']\n"
        ),
    ),
    SeededBug(
        bug_id="SM-03",
        title="identifier_case rewrites quoted identifiers",
        report=(
            "Formatting with identifier_case='upper' also uppercases quoted identifiers. "
            "'select * from \"MyTable\"' becomes 'select * from \"MYTABLE\"', which changes "
            "what the SQL means — a quoted identifier is case-sensitive and must be left alone."
        ),
        path="sqlparse/filters/tokens.py",
        # Anchored on `ttype = T.Name, T.String.Symbol` because the broken line alone
        # is character-for-character identical to the one in the _CaseFilter base
        # class — the harness self-check caught the ambiguity, which is what it is for.
        correct=(
            "    ttype = T.Name, T.String.Symbol\n\n"
            "    def process(self, stream):\n"
            "        for ttype, value in stream:\n"
            "            if ttype in self.ttype and value.strip()[0] != '\"':"
        ),
        broken=(
            "    ttype = T.Name, T.String.Symbol\n\n"
            "    def process(self, stream):\n"
            "        for ttype, value in stream:\n"
            "            if ttype in self.ttype:"
        ),
        hidden_test_path="tests/test_sm03_hidden.py",
        hidden_test=(
            "import sqlparse\n\n\n"
            "def test_quoted_identifiers_keep_their_case():\n"
            "    sql = 'select * from \"MyTable\"'\n"
            "    assert '\"MyTable\"' in sqlparse.format(sql, identifier_case='upper')\n\n\n"
            "def test_unquoted_identifiers_are_still_uppercased():\n"
            "    assert 'FOO' in sqlparse.format('select * from foo', identifier_case='upper')\n"
        ),
    ),
    SeededBug(
        bug_id="SM-04",
        title="truncate_strings keeps one character too few",
        report=(
            "format(sql, truncate_strings=3) truncates one character too early: "
            "\"select 'abcdefghij'\" gives 'ab[...]' when it should give 'abc[...]'. "
            "The truncated string should keep exactly `truncate_strings` characters."
        ),
        path="sqlparse/filters/tokens.py",
        correct="                value = ''.join((quote, inner[:self.width], self.char, quote))",
        broken="                value = ''.join((quote, inner[:self.width - 1], self.char, quote))",
        hidden_test_path="tests/test_sm04_hidden.py",
        hidden_test=(
            "import sqlparse\n\n\n"
            "def test_truncate_keeps_exactly_the_requested_width():\n"
            "    assert sqlparse.format(\"select 'abcdefghij'\", truncate_strings=3) == (\n"
            "        \"select 'abc[...]'\"\n"
            "    )\n"
        ),
    ),
]


def by_id(bug_id: str) -> SeededBug:
    match = next((b for b in BUGS if b.bug_id == bug_id), None)
    if match is None:
        raise KeyError(f"unknown bug {bug_id!r} — have {[b.bug_id for b in BUGS]}")
    return match
