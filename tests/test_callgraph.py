"""O7 — one-hop callee expansion (`docs/limitations.md` §8).

The mechanism these cover is small; the two properties worth protecting are not.

First, **it is off unless asked.** The pack is embedded in the prompt and the prompt
keys the replay fixtures, so a default flip would invalidate the offline demo — a
test asserts the default rather than leaving it to a code review.

Second, **a callee is placed behind its caller, not at the end.** That is the whole
reason the hop survives the token budget: appended last it would be the first thing
the cap drops, which on SM-01 is precisely the chunk the hop exists to fetch.
"""

from __future__ import annotations

from forge.models import Chunk, ChunkKind, SearchHit
from forge.rag.callgraph import build_symbol_index, callees, resolve_callees
from forge.rag.pack import expand_hits_with_calls, pack_context


def chunk(symbol: str, raw: str, *, path: str = "pkg/a.py", line: int = 1, kind=ChunkKind.FUNCTION):
    return Chunk(
        chunk_id=f"{path}:{symbol}",
        repo="t",
        path=path,
        language="python",
        kind=kind,
        symbol=symbol,
        start_line=line,
        end_line=line + raw.count("\n"),
        text=raw,
        raw=raw,
    )


# --- extraction ------------------------------------------------------------


def test_calls_are_found_structurally_not_textually():
    """A name in a docstring or a comment is not a call — that is why this uses the
    parser and not a regex."""
    source = (
        "def get_parent_name(self):\n"
        '    """Uses remove_quotes to strip. See also strip_quotes below."""\n'
        "    # remove_quotes handles the backtick case\n"
        "    return normalise(self.value)\n"
    )
    assert callees(source) == ["normalise"]


def test_a_method_call_contributes_its_leaf_name():
    """``self._get_first_name`` is indexed under ``_get_first_name``, so that is the
    name a lookup has to be able to use."""
    source = "def get_real_name(self):\n    return self._get_first_name(real_name=True)\n"

    assert callees(source) == ["_get_first_name"]


def test_an_indented_fragment_still_parses():
    """Chunks are fragments — an indented method body is not a valid module. The
    parser is error-tolerant and recovers the calls inside one anyway, which is the
    reason this does not use ``ast``."""
    source = (
        "    def _get_first_name(self, idx=None):\n"
        "        for token in self.tokens:\n"
        "            return remove_quotes(token.value)\n"
    )
    assert "remove_quotes" in callees(source)


def test_builtins_are_not_resolved():
    """``isinstance`` and ``len`` resolve to nothing, or worse to a same-named method
    that has nothing to do with the call. Either way they spend the per-chunk cap."""
    source = "def f(x):\n    if isinstance(x, str) and len(x) > 0:\n        return parse_sql(x)\n"

    assert callees(source) == ["parse_sql"]


def test_extraction_is_capped_and_in_source_order():
    source = "def f():\n    a_one()\n    b_two()\n    c_three()\n    d_four()\n"

    assert callees(source, limit=3) == ["a_one", "b_two", "c_three"]


# --- resolution ------------------------------------------------------------


def _index(*chunks):
    return build_symbol_index({(c.path, c.symbol.split(".")[0]): [c] for c in chunks})


def test_a_callee_resolves_to_its_definition_chunk():
    caller = chunk("get_parent_name", "def get_parent_name(self):\n    return remove_quotes(v)\n")
    target = chunk("remove_quotes", "def remove_quotes(val):\n    return val\n", path="pkg/u.py")

    assert resolve_callees(caller, _index(caller, target)) == [target]


def test_an_ambiguous_name_resolves_in_the_caller_s_own_file():
    """``get_real_name`` exists on three classes in sqlparse/sql.py. A leaf name cannot
    disambiguate them, but the file being read is the likelier referent."""
    caller = chunk("A.f", "def f(self):\n    return self.get_real_name()\n", path="pkg/here.py")
    elsewhere = chunk("B.get_real_name", "def get_real_name(self):\n    pass\n", path="pkg/far.py")
    here = chunk("A.get_real_name", "def get_real_name(self):\n    pass\n", path="pkg/here.py")

    assert resolve_callees(caller, _index(caller, elsewhere, here)) == [here]


def test_recursion_does_not_resolve_to_itself():
    recursive = chunk("walk", "def walk(node):\n    return walk(node.parent)\n")

    assert resolve_callees(recursive, _index(recursive)) == []


def test_an_unresolvable_call_is_skipped_silently():
    """Third-party and stdlib calls have no chunk. Not finding one is the normal case."""
    caller = chunk("f", "def f():\n    return requests_get(url)\n")

    assert resolve_callees(caller, _index(caller)) == []


# --- packing ---------------------------------------------------------------


def test_the_callee_lands_directly_behind_its_caller():
    """Not at the end. This is the property that makes the hop survive the cap."""
    caller = chunk("get_parent_name", "def get_parent_name(self):\n    return remove_quotes(v)\n")
    other = chunk("unrelated", "def unrelated():\n    pass\n", path="pkg/z.py")
    target = chunk("remove_quotes", "def remove_quotes(val):\n    return val\n", path="pkg/u.py")

    groups = {
        ("pkg/a.py", "get_parent_name"): [caller],
        ("pkg/z.py", "unrelated"): [other],
        ("pkg/u.py", "remove_quotes"): [target],
    }
    hits = [SearchHit(chunk=caller, score=1.0), SearchHit(chunk=other, score=0.5)]

    out = expand_hits_with_calls(hits, groups)

    assert [c.symbol for c in out] == ["get_parent_name", "remove_quotes", "unrelated"]


def test_the_hop_survives_a_budget_that_would_have_cut_it_off_the_end():
    """The SM-01 shape in miniature: the fix site is reachable from rank 1, and the
    budget stops well before the end of the expanded list."""
    caller = chunk("get_parent_name", "def get_parent_name(self):\n    return remove_quotes(v)\n")
    target = chunk("remove_quotes", "def remove_quotes(val):\n    return val\n", path="pkg/u.py")
    filler = [
        chunk(f"pad{i}", "def pad():\n" + "    x = 1\n" * 40, path=f"pkg/p{i}.py") for i in range(6)
    ]

    groups = {(c.path, c.symbol.split(".")[0]): [c] for c in [caller, target, *filler]}
    hits = [SearchHit(chunk=caller, score=1.0)] + [SearchHit(chunk=f, score=0.5) for f in filler]

    packed = pack_context(hits, groups=groups, token_budget=250, expand_calls=True)

    assert "remove_quotes" in [c.symbol for c in packed.chunks]
    assert len(packed.chunks) < len(hits) + 1, "the budget must actually have bitten"


def test_expansion_is_off_by_default():
    """The pack keys the replay fixtures. A silent default flip is a broken demo."""
    from forge.config import Settings

    caller = chunk("get_parent_name", "def get_parent_name(self):\n    return remove_quotes(v)\n")
    target = chunk("remove_quotes", "def remove_quotes(val):\n    return val\n", path="pkg/u.py")
    groups = {(c.path, c.symbol.split(".")[0]): [c] for c in [caller, target]}
    hits = [SearchHit(chunk=caller, score=1.0)]

    assert Settings(_env_file=None).retrieval_expand_calls is False
    assert [c.symbol for c in pack_context(hits, groups=groups).chunks] == ["get_parent_name"]
