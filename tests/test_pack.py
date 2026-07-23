"""Parent-document expansion and token-budget packing — no index, no network.

Fake chunks and hits exercise the group logic and the greedy packer directly; the
same logic is what the ablation's "+ parent expansion" row measures.
"""

from __future__ import annotations

from forge.models import Chunk, ChunkKind, SearchHit
from forge.rag.pack import expand_hits, group_key, pack_context


def _chunk(cid: str, path: str, symbol: str | None, start: int, end: int, raw: str = "x") -> Chunk:
    return Chunk(
        chunk_id=cid,
        repo="r",
        path=path,
        language="python",
        kind=ChunkKind.METHOD if symbol and "." in symbol else ChunkKind.FUNCTION,
        symbol=symbol,
        start_line=start,
        end_line=end,
        text=raw,
        raw=raw,
    )


def _hit(chunk: Chunk, score: float = 1.0) -> SearchHit:
    return SearchHit(chunk=chunk, score=score)


def test_group_key_rolls_a_method_up_to_its_class():
    assert group_key(_chunk("1", "a.py", "Lexer.get_tokens", 10, 20)) == ("a.py", "Lexer")
    assert group_key(_chunk("2", "a.py", "Lexer", 1, 30)) == ("a.py", "Lexer")
    assert group_key(_chunk("3", "a.py", "split", 1, 5)) == ("a.py", "split")
    assert group_key(_chunk("4", "a.py", None, 1, 5)) == ("a.py", "<module>")


def test_expand_hits_pulls_in_siblings_at_the_group_rank():
    cls = _chunk("c", "a.py", "Lexer", 1, 30)
    m1 = _chunk("m1", "a.py", "Lexer.scan", 5, 10)
    m2 = _chunk("m2", "a.py", "Lexer.emit", 12, 18)
    other = _chunk("o", "b.py", "helper", 1, 4)
    groups = {("a.py", "Lexer"): [cls, m1, m2], ("b.py", "helper"): [other]}

    # A single method hit expands to the whole Lexer group, ordered by line.
    out = expand_hits([_hit(m2), _hit(other)], groups)
    assert [c.chunk_id for c in out] == ["c", "m1", "m2", "o"]


def test_expand_hits_emits_a_group_once():
    cls = _chunk("c", "a.py", "Lexer", 1, 30)
    m1 = _chunk("m1", "a.py", "Lexer.scan", 5, 10)
    groups = {("a.py", "Lexer"): [cls, m1]}
    # Two hits in the same group must not duplicate its members.
    out = expand_hits([_hit(m1), _hit(cls)], groups)
    assert [c.chunk_id for c in out] == ["c", "m1"]


def test_pack_context_without_expansion_keeps_hit_order():
    hits = [_hit(_chunk(str(i), "a.py", f"f{i}", i, i)) for i in range(3)]
    pack = pack_context(hits, expand=False, queries=["q"])
    assert [c.chunk_id for c in pack.chunks] == ["0", "1", "2"]
    assert pack.queries == ["q"]


def test_pack_context_stops_at_the_token_budget_but_admits_the_first():
    big = "word " * 400  # ~500 tokens at 4 chars/token
    hits = [_hit(_chunk(str(i), "a.py", f"f{i}", i, i, raw=big)) for i in range(5)]
    pack = pack_context(hits, expand=False, token_budget=600)
    # First always admitted; the second would exceed 600 tokens, so packing stops.
    assert [c.chunk_id for c in pack.chunks] == ["0"]


def test_pack_context_expands_when_given_groups():
    cls = _chunk("c", "a.py", "Lexer", 1, 30)
    m1 = _chunk("m1", "a.py", "Lexer.scan", 5, 10)
    groups = {("a.py", "Lexer"): [cls, m1]}
    pack = pack_context([_hit(m1)], groups=groups, token_budget=10_000)
    assert {c.chunk_id for c in pack.chunks} == {"c", "m1"}
