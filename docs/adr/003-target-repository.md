# ADR-003 — Target repository: `sqlparse`

**Status:** accepted · **Date:** 2026-07-23 · **Cahier:** §15 (demo) · **Descope:** §9

## Context

FORGE is judged on a live demo against a real codebase, and everything downstream of
retrieval keys off which one: the golden set (D3), the RAG ablation (D4), and the seeded
`swe_mini` bugs with their hidden test suite (D8). The choice was the standing blocker **B1**.
It needs a repository that is small enough to index and reason about on a CPU-only laptop, real
enough to be convincing, and hermetic enough to run inside the sandbox — which executes tests
with `--network=none`. Concretely: 3–5k LOC of Python (descope §9), a genuine `pytest` suite, no
compiled extensions, no network in its tests, clear named subsystems (so *"where is X handled"* has
crisp answers), and self-contained logic that yields deterministic bugs to seed.

## Decision

**`sqlparse` 0.5.5**, pinned at commit `0d240230939bfb3b751b504878b1c7df04a3cab3`, cloned into
`data/target` (git-ignored; `TARGET_REPO` defaults there, so no `.env` override is needed).

It is a non-validating SQL parser and formatter: **4 146 LOC**, **zero runtime dependencies**, a
fully offline `pytest` suite, and textbook subsystems — `lexer` + `keywords` (tokenising),
`engine.grouping` + `engine.statement_splitter` (parse tree), `filters` (transforms), `formatter`
(the public pipeline). A parser is also the ideal `swe_mini` target: an off-by-one in tokenising or
grouping fails a specific test deterministically, with no timezone or I/O flakiness.

## Alternatives

| Option | Rejected because |
|---|---|
| **marshmallow** | Equally clean and pure-Python, but a heavier test setup and a serialization domain with fuzzier "where is X" boundaries than a lexer→parser→formatter pipeline. |
| **arrow** | Good size and hermetic, but datetime/locale logic makes seeded bugs timezone-sensitive — flakier for the repair loop's pass/fail signal. |
| **click / jinja2 / rich** | All over the 5k ceiling, and click/jinja pull optional C or template-compilation paths that complicate the sandbox. |

## Consequences

- The golden set and ablation are keyed by `chunk_id`, which hashes repo/path/symbol/line, **not
  content** — so the same `evals/golden/code.jsonl` scores every embedding candidate in D4's
  ablation without re-verification. The pin is what makes those ids stable.
- First quality measurement, MiniLM + hybrid: **Recall@10 = 0.40** (`docs/evaluation.md`). Low, and
  informative — D4's ablation now has a concrete baseline to beat, and a concrete reason
  (test-corpus pollution) to investigate.
- Indexing the whole repo includes `tests/`, which the baseline shows can outrank implementations.
  Left in for now: narrowing the corpus is a D4 tuning decision, not a D3 one.
