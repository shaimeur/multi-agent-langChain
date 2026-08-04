# FORGE — architecture

What the system is, how a request moves through it, and why each part is shaped the
way it is. The specification is `cahier-des-charges.md`; the deviations that actually
govern the build are in `descope-v1.md`. This document describes what was built.

---

## 1. The shape of it

One Python package, `src/forge/`, plus a separate React build in `web/`.

```
              ┌──────────────┐        ┌──────────────┐
   browser ──▶│  web/ (SPA)  │───────▶│  api/ (Fast) │
              └──────────────┘  /v1   └──────┬───────┘
                                             │
   terminal ──▶ cli/ ───────────────────────▶│
                                             ▼
                                    ┌────────────────┐
                                    │  core/ graph   │  LangGraph + SQLite
                                    │  (LangGraph)   │  checkpointer
                                    └───┬────────┬───┘
                                        │        │
                        ┌───────────────┘        └──────────────┐
                        ▼                                       ▼
                ┌───────────────┐                       ┌───────────────┐
                │  rag/         │                       │  sandbox/     │
                │  Qdrant +     │                       │  one throwaway│
                │  BM25 + rg    │                       │  container/run│
                └───────────────┘                       └───────────────┘
                        │                                       │
                        └──────────────┬────────────────────────┘
                                       ▼
                               ┌───────────────┐
                               │  guardrails/  │  wraps both, logs everything
                               └───────────────┘
```

| Package | Responsibility |
|---|---|
| `core/` | the LangGraph state machine, the six agent nodes, workspaces, checkpointing |
| `rag/` | walk → chunk → embed → index; hybrid retrieval; grounded answering |
| `sandbox/` | running untrusted code in a hardened, throwaway container |
| `guardrails/` | the three defence layers and the queryable event log |
| `tools/` | the ten externally-callable tools (C6) |
| `mcp/` | the same ten, reflected over MCP — no capability of its own (C6) |
| `api/` | the §11 HTTP surface, SSE streaming, session store |
| `cli/` | `forge config | index | search | ask | tools | mcp | fix` |
| `llm/` | provider construction and the fixture cache that makes runs replayable |

---

## 2. Two paths, not one

This is the single most important thing to understand, and the thing most easily
missed from the UI: **a question and a change request run different graphs.**

### The ask path — `POST /v1/ask`, `forge ask`, the UI's *Ask* mode

```
question ──▶ sentinel_in ──▶ hybrid retrieval ──▶ ground_answer ──▶ sentinel_out ──▶ answer
```

Stateless, single model call, no gates. `ground_answer` numbers the retrieved
snippets, asks for `[n]` citations, and then **verifies every citation in code**
against the pack that was actually retrieved (`ContextPack.supports`). `grounded:
false` is the honest signal that nothing verifiable was cited — not a silent
hallucination.

### The change path — `POST /v1/sessions/{id}/messages`, `forge fix`, the UI's *Change* mode

```
START ──▶ retriever ──▶ planner ──▶ [PLAN GATE] ──▶ regression ──▶ editor
                           ▲                                        │
                           │ needs_more_context (×1)                ▼
                           └──────────────────────────────  [PATCH GATE]
                                                                    │
                                          reviewer ◀── verify ◀── apply
                                             │
                                    revise ──┴── approve ──▶ END
```

Stateful, checkpointed, and it stops twice for a human.

**The two paths are not unified.** `Route` is `{RETRIEVE, ANSWER, END}` — the
SUPERVISOR routes *within* the ask path and has no member for "this is a change
request". The session stream therefore always builds the change graph, so the UI, not
the supervisor, decides which path a message takes. This is recorded as **O8**; the
cahier's §4 puts routing in the supervisor, and closing that gap means adding
`Route.CHANGE`, an intent field on the state, and a conditional edge after the
retriever.

---

## 3. The six agents

Each is a node returning a state delta; none of them writes to disk except `apply`.

| Agent | Input | Output | Refuses to |
|---|---|---|---|
| **SUPERVISOR** | the turn | `RouteDecision` | generate content — it routes and nothing else |
| **RETRIEVER** | a question | `ContextPack` | trust retrieved text (it scans it for injection first) |
| **PLANNER** | pack + request | `ChangePlan` | emit a step that cites nothing |
| **EDITOR** | plan + pack | `PatchSet` | touch the filesystem — it returns edits, `apply` writes them |
| **SANDBOX_ENGINEER** | workspace | `ExecutionReport` | run anything outside the container |
| **REVIEWER** | plan, diff, report | `ReviewVerdict` | approve a patch whose tests did not run |

**Why six and not one agent with tools.** Each boundary is a place where a typed
object is validated. A single agent holding all six responsibilities can decide to
skip its own citation check; six nodes with typed payloads in `models.py` cannot,
because the check lives in the edge between them, not in a prompt. The plan gate is
wired to the planner's *success* exit by construction (`build_change_graph`), so a
node cannot route around it.

**The repair loop** is the collaboration form worth demonstrating: the reviewer
returns `revise`, the editor sees the sandbox's actual failures, and the second patch
is better than the first. Observed on 2026-08-03: `isinstance(sql, str)` →
`isinstance(sql, (str, bytes, TextIOBase)) and not hasattr(sql, 'read')`, after the
sandbox reported 28 regressions the first patch had caused.

---

## 4. Retrieval

```
repo ──▶ walker ──▶ chunkers (tree-sitter AST) ──▶ embed ──▶ Qdrant
                                                    └──────▶ BM25
```

- **Chunking is AST-based, not fixed-window.** A chunk is a function or a class, so a
  citation lands on something a person can read. Python only — `.py/.pyi` via
  tree-sitter, plus markdown and config files. Multi-language was cut on D2.
- **Retrieval is hybrid**: dense (MiniLM) + sparse (BM25) + ripgrep, fused with
  reciprocal-rank fusion. An identifier-shaped query (`parse_config`,
  `Lexer::scan`) skips the dense leg entirely — for an exact symbol, lexical wins.
- **Parent expansion**: a matched method can be widened to its class, budget allowing.
- **The reranker is built but off in the live path** (descope §3): D4 measured it
  costing more latency than it returned in nDCG. It stays in the eval harness so the
  claim is measured rather than asserted.

**The known limit (O7), and the switch for it.** Retrieval is text similarity: it does
not follow a call graph, so a bug report describing a symptom at `get_real_name` does
not surface `utils.remove_quotes` — the fix site sits at rank 28, far outside the live
top-8, and ranks 2nd only once the report *names* it.

`rag/callgraph.py` walks one hop: tree-sitter extracts what a retrieved chunk calls and
each name resolves against the indexed chunks, with the callee placed directly behind its
caller so it survives the token cap. `RETRIEVAL_EXPAND_CALLS` turns it on, and it is off,
because the golden set measures **no gain** from it for 11.8 % more tokens — its 42
questions all name the symbol they want, so there is no hop to bridge. `limitations.md` §8
has the table and the argument.

---

## 5. Guardrails — three layers

| Layer | Where | Catches |
|---|---|---|
| `sentinel_in` | before retrieval | oversized input, secrets in the prompt, direct injection, out-of-scope requests |
| `injection` | on retrieved chunks, on **both** paths | §8.2 indirect injection — a malicious instruction planted in someone else's code |
| `policy` | before any tool or filesystem call | path escapes, denied components, forbidden git verbs |
| `sentinel_out` | before anything leaves | unverifiable citations, secrets in generated code, diffs that do not apply |

Every decision is an event — `allowed` as deliberately as `blocked`, because a log
that only records refusals cannot show that a check ran on a clean run. Events are
queryable by session, stage and action (`GET /v1/guardrails/events`).

The tier-2 scan is the interesting one. A comment reading *"Ignore all previous
instructions…"* planted in `sqlparse/lexer.py` was **redacted from the pack before the
planner saw it**, logged as `injection.override`, and the model never acted on it.

**Both doors, one seam (O6, closed D15).** The scan runs where third-party text becomes
prompt, and there are two such lines, not one: `core/agents/retriever.py` for the change
path and `rag/answer.py` for the ask path. Until D15 only the first called it, so a
poisoned comment reached the prompt through `POST /v1/ask` — which is the route the UI's
*Ask* button uses. Both now scan. The property that let this land at freeze time without
touching a single replay fixture: `scan_chunks` returns a clean chunk as the *same
object*, so on an unpoisoned corpus the prompt is byte-identical.

---

## 6. The sandbox

One throwaway container per run, via the Docker SDK:

`--network=none` · read-only root · non-root user · `cap_drop=ALL` ·
`no-new-privileges` · memory, CPU and PID caps · `RLIMIT_DATA` · output truncation ·
wall-clock timeout · `/work` is the bind-mounted worktree and nothing else.

**The exit code is the authority.** Not the model's opinion of whether the patch
worked — `pytest`'s exit status inside a box that cannot reach the network.

The image is dependency-free by design (pytest and ruff only), so a patch cannot
smuggle in a package. Where Docker is unavailable the executor degrades to
`subprocess` + `resource.setrlimit`, and that degradation is documented rather than
hidden (`limitations.md`).

Under `docker compose`, the api container deliberately does **not** mount
`/var/run/docker.sock`: a container holding the Docker socket is root-equivalent on
the host, which is strictly worse than the exposure the sandbox exists to prevent. So
compose runs the fallback, and full hardening is the host-side path.

---

## 7. State, memory and the gates

LangGraph over an `AsyncSqliteSaver` checkpointer (Postgres was dropped — descope §1).
Every node returns a delta; the checkpoint is written after each.

`interrupt()` at the two gates is what makes an approval durable: the state is
persisted and the run *stops*. `POST /approve` resumes it with `Command(resume=…)`.
An approval therefore survives a closed browser tab, an hour, or a process restart —
which is also how C4 is demonstrated: two turns of one session in two different
processes, sharing nothing but the SQLite file.

---

## 8. Offline by construction

Every model call goes through a fixture cache keyed on the prompt and the model.

- `CACHE_MODE=auto` — serve from disk, otherwise call and record.
- `CACHE_MODE=replay` — **disk only; a miss raises.** This is what the demo runs, and
  what `tests/conftest.py` forces so the suite never touches the network.

A miss being fatal rather than falling through to the network is the whole guarantee:
it is what lets the demo run with no key, no quota and no wifi.

The fragile part is that a fixture key includes the *prompt*, and the prompt embeds
retrieved snippets — so **re-indexing can invalidate every recorded fixture**. An
incremental index whose HEAD moved backwards once reordered the top-8 and broke every
ask fixture at once. Always re-index `--full` after restoring the target repo, then
run `forge ask` in replay to confirm.
