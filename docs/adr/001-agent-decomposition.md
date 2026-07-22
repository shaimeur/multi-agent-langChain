# ADR-001 — Six agents, and why control is split from content

**Status:** accepted · **Date:** 2026-07-22 · **Cahier:** §4

## Context

The requirement is "at least 4 specialised agents that collaborate." The lazy reading is to take one
agent with tools and give it four prompts. The jury will ask why this is not that.

## Decision

Six agents, each a LangGraph node with its own system prompt, its own bound tool subset, and its own
output schema:

| Agent | Owns | Model tier |
|---|---|---|
| `SUPERVISOR` | control flow only, never content | router |
| `RETRIEVER` | all knowledge access — the only node touching the index | router |
| `PLANNER` | intent + context → an ordered, reviewable `ChangePlan` | reasoner |
| `EDITOR` | one plan step → a `PatchSet`. Never writes to disk | coder |
| `SANDBOX_ENGINEER` | tests, and execution in an isolated container | coder |
| `REVIEWER` | the five-point quality gate; `APPROVE` or `REVISE` | reasoner |

`SENTINEL` is deliberately **not** an agent — see ADR-004.

## Why not merge Supervisor into Planner

The obvious challenge, so the answer needs to be ready:

1. **Cost.** Routing is a classification, not a reasoning task. Separating it puts a cheap model on
   the hot path — and with ~29 LLM calls per run, the hot path is where the quota goes.
2. **Testability.** A supervisor emitting `RouteDecision` through `with_structured_output` is a
   deterministic-ish function that can be unit-tested against fixed states. A supervisor that also
   reasons about code cannot.
3. **Contamination.** The planner's chain-of-thought would otherwise sit in the routing context and
   bias it.

## Why six rather than four

Four would satisfy the letter of the requirement. Six is the natural decomposition of *this*
problem, and it is what produces a **loop** rather than a pipeline: `EDITOR → SANDBOX_ENGINEER →
REVIEWER → EDITOR`. The loop is the collaboration evidence — a linear pipeline demonstrates
delegation but never disagreement.

The Editor/Sandbox split specifically: the thing that writes code must not also be the thing that
decides whether it works. That separation is the whole value proposition.

## Consequences

- Six prompt surfaces to maintain, and six ways to be inconsistent. Mitigated by shared Pydantic
  schemas as the contract between nodes rather than prose.
- Reviewer should run on a *different model family* from Editor, so the critic does not inherit the
  author's blind spots. This is real but it is last on the cut list (`descope-v1.md` §12): under one
  provider the roles collapse onto one family and the limitation gets stated in the report.
- The budget guard lives in the Supervisor, because it is the only node that sees every transition.
