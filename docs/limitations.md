# Limitations

What FORGE does **not** guarantee. Cahier §17, deliverable L2.

The entry that matters most is §1: FORGE executes code a model wrote, so the honest
question is not "is it sandboxed" but "sandboxed *how*, and what happens when that
isolation is unavailable". Every claim here is stated as a gap rather than softened,
because a security property nobody can check is worth less than a documented hole.

---

## 1 — The sandbox fallback has no isolation worth the name

**Where:** `src/forge/sandbox/runner.py` · **Cahier:** §8.3 · **Since:** D7

FORGE runs tests and model-written code in one of two backends. Which one ran is
recorded on every `ExecutionReport` as `isolation`, so a result can never quietly
claim more confinement than it had — and `SANDBOX_BACKEND=docker` refuses to run at
all rather than downgrade.

| | `Isolation.DOCKER` | `Isolation.SUBPROCESS` (fallback) |
|---|---|---|
| Network egress | refused — `--network=none` | **not blocked — full host network** |
| Host filesystem | only the worktree is mounted | **entire filesystem readable as your user** |
| Root filesystem | read-only | **writable wherever your user can write** |
| Process cap | `--pids-limit=128` | **none** — see below |
| Memory cap | 512 MB cgroup, swap included | `RLIMIT_DATA` 512 MB, heap only |
| CPU cap | 1.0 CPU | `RLIMIT_CPU`, wall-clock deadline |
| Privileges | non-root, `cap_drop=ALL`, `no-new-privileges` | **your own uid and privileges** |
| Output | capped by the log driver + a 64 KB read | `RLIMIT_FSIZE` 64 MB + a 64 KB read |
| Wall clock | hard deadline, `SIGKILL` | hard deadline, `SIGKILL` to the process group |

**The gap in one sentence:** on the fallback, a hostile patch runs with your user's
privileges on your real filesystem with working network access. It is a development
and CI convenience, not a security boundary. Anything resembling a real deployment
must set `SANDBOX_BACKEND=docker`, which turns a missing socket into a hard error.

Two specific decisions inside the fallback, both deliberate:

- **No `RLIMIT_NPROC`,** so a fork bomb is *not* contained by a pid cap. `RLIMIT_NPROC`
  caps processes per real uid rather than per process tree: on a busy desktop it
  either trips immediately on the developer's own process count or fails to bound the
  tree at all. Containment rests on the process-group `SIGKILL` at the deadline and on
  `RLIMIT_CPU`. A fork bomb will therefore load the machine until the deadline fires.
  The pid cap is the container's job, and the fork-bomb test is container-only.
- **`RLIMIT_DATA`, not `RLIMIT_AS`,** for the memory cap. `RLIMIT_AS` bounds reserved
  address space, which threaded Rust and Go binaries reserve far more of than they
  touch: measured on this machine, `ruff` aborted with SIGABRT in **8 of 10 identical
  runs** under a 512 MB `RLIMIT_AS`, and the result moved with unrelated environment
  changes. `RLIMIT_DATA` passed 10 of 10 while still refusing a deliberate 1 GB
  allocation. Caveat: `RLIMIT_DATA` only covers `mmap` on **Linux ≥ 4.7**; on an older
  kernel it bounds `brk` alone and a large `mmap` slips past it.

The fallback also borrows `pytest` and `ruff` from the *development* virtualenv,
because it has no image to get them from — so the sandboxed code shares an
interpreter with FORGE's own dependencies. Neither backend inherits this process's
environment, so the provider API keys stay out of both (asserted on both backends in
`tests/test_sandbox.py`).

## 2 — Output is truncated at the head, so a huge transcript loses its summary

**Where:** `src/forge/sandbox/runner.py` · **Since:** D7

Captured output is cut at `sandbox_max_output_bytes` (64 KB) and the cut keeps the
*first* 64 KB, not the last. Tailing would mean pulling the whole transcript through
the socket first, which is the denial of service the cap exists to survive.

The cost is that a run which prints more than 64 KB loses pytest's trailing summary
line, so `passed` / `failed` / `skipped` can read 0 on a run that really did execute
tests. This is why `outcome` is derived from the **exit code** and never from the
parsed counts: the verdict stays correct even when the detail is gone, and
`truncated` is set on the report so the loss is visible rather than silent.

## 3 — Coverage is a percentage, not a delta

**Where:** `src/forge/sandbox/tools.py` · **Since:** D7

`ExecutionReport.coverage_percent` is a single number parsed from pytest-cov's
`TOTAL` row. The "coverage delta" the roadmap asks for is the subtraction of two
reports (before and after a patch) and belongs to whoever holds both — the repair
loop at D8. A lone report has nothing to compare against, so no delta field is
offered rather than one that is misleadingly always zero.

## 4 — Container start-up costs ~85 ms per run

**Where:** `src/forge/sandbox/runner.py` · **Since:** D7

One throwaway container per run, created and removed each time. Measured on this
machine, `python -c pass` over 7 warm runs:

| Backend | Median | Min | Max |
|---|---:|---:|---:|
| `subprocess` | 17.8 ms | 9.8 ms | 18.3 ms |
| `docker` | 102.8 ms | 88.0 ms | 111.1 ms |

So ~85 ms of container overhead per invocation, which a real pytest run (~0.5 s)
mostly hides. A warm pooled container would amortise it, but pooling means reusing a
filesystem and a process namespace between runs — exactly the property that makes an
ephemeral container trustworthy. At the repair loop's scale, a handful of runs per
session, 85 ms is not worth trading that for.

## 5 — Under `docker compose up`, the sandbox runs on the fallback

**Where:** `docker-compose.yml` · **Since:** D7

For the API container to spawn hardened sandbox containers it would need
`/var/run/docker.sock` mounted into it. A container holding the Docker socket can
start a privileged container mounting the host root filesystem — it is
**root-equivalent on the host**, a strictly worse exposure than the one the sandbox
exists to prevent. FORGE therefore does not mount it, and the choice is default-off
rather than a flag nobody reads.

The consequence: under `docker compose up`, `run_in_sandbox` finds no socket and
degrades to the §1 fallback *inside the api container*. That container is itself an
isolation boundary, so the blast radius is the api container rather than the host —
but within it the §1 gaps all apply, and the api container does have network access.

| Deployment | Backend | Boundary |
|---|---|---|
| `forge` on a host with Docker | `docker` | ephemeral container, per run |
| `docker compose up` | `subprocess` | the api container itself |
| Host without Docker | `subprocess` | **none** — see §1 |

Uncomment the socket mount in `docker-compose.yml` only if you accept
root-equivalent host access in exchange for per-run containers.

## 6 — Direct injection is flagged, not blocked (a deviation from §13.4)

**Where:** `src/forge/guardrails/sentinel_in.py` · **Since:** D10 · **Cases:** SEC-01, SEC-02

Cahier §13.4 expects *"Injection directe → bloquée à `sentinel_in`, événement
journalisé"*. FORGE logs the event and lets the turn proceed.

The reason is that FORGE is a *coding assistant*, and "how would a prompt injection in
this repo's comments affect you?" is a legitimate, on-topic engineering question. With
tier-1 heuristics alone there is no way to separate a user *asking about* an override
phrase from a user *issuing* one — that separation is exactly what §8.1's tier-2
classifier exists to provide, and tier 2 is not built (§7 below). Blocking on the
phrase would refuse a real question about the very attack the system defends against,
which is a bad trade when the phrase is not what makes the attack work.

What actually stops the attack is downstream and unaffected: retrieved content is
spotlighted and stripped, and the tool and path whitelists are literal constants that
no text — from a user or a repository — can widen. A model that *did* get confused by
a user's phrasing still could not act on it.

Reconsider if tier 2 lands: with a classifier able to score intent, blocking the
high-confidence band becomes cheap and the flag-only posture stops being necessary.

## 7 — Injection tier 2 is not built

**Where:** `src/forge/guardrails/injection.py` · **Since:** D10

§8.1 specifies three tiers: cheap heuristics → a DeBERTa-class classifier → an LLM
judge on the ambiguous middle. Only tier 1 exists. `classify()` is the seam a tier 2
would drop into.

Two reasons, both measured or structural rather than preferential. A per-chunk
transformer would run on **every chunk of every pack**; D4 measured a comparable
cross-encoder taking p95 from 14 ms to 2589 ms on this CPU-only box, and that cost was
already judged unacceptable once (the reranker ships off, descope §3). The judge tier
needs a provider key, which is blocker B2.

The consequence is a real one and is not hidden by the flag-only posture above: novel
phrasings that no heuristic matches pass tier 1 undetected. The mitigations that do
not depend on detection — spotlighting, instruction stripping of known patterns, and
privilege invariance — are what carries the load in that case.

<!-- Entries below this line are not sandbox-related. Add new ones with the same
     shape: where, since which day, and the gap stated plainly. -->

## 8 — Retrieval cannot bridge a call hop

**Where:** `src/forge/core/agents/retriever.py` · **Since:** D14 (measured on `swe_mini` SM-01)

Retrieval scores chunks against the *words of the question*. It has no notion of "and
whatever that function calls". When the symbol naming the defect is one the report
never mentions, it is not retrieved, and no amount of `k` fixes it.

SM-01 is the concrete case. The bug report names `get_real_name`; the actual defect is
two call hops down, in `utils.remove_quotes`. That chunk is **absent from the top 35** —
yet it ranks 2nd the moment the query names it. The retrieval is not weak, it is
answering a different question from the one that matters.

The repair loop cannot recover from this on its own. The planner's `missing` field is
the designed escape hatch, but it asks for the file the *report* implicated, so the
retry returns the same neighbourhood and the second pass fails for the first reason.

This is why `docs/evaluation.md`'s 4/4 on `swe_mini` is scoped to the repair loop: that
harness hands the agent the correct file rather than retrieving it. End to end, from a
bug report alone, SM-01 fails before the loop starts.

### The fix is built, measured, and shipped **off** (D15)

`src/forge/rag/callgraph.py` does the hop: tree-sitter extracts the names a retrieved
chunk calls — structurally, so a name in a comment is not a call — and each resolves
against the indexed chunks. `pack_context(expand_calls=True)` places a callee directly
behind its caller rather than at the end of the pack, which is what lets it survive the
token cap. `RETRIEVAL_EXPAND_CALLS` is the switch.

Two corrections to what is written above, both found by measuring rather than assuming:

- `remove_quotes` is **not** absent from the top 35 — it sits at **rank 28**. The
  operative number is the live `retrieval_top_k` of **8**, which it misses by a long way.
- It is two hops from `get_real_name`, but only **one** from something retrieval already
  finds: **rank 6** is `TokenList._get_first_name`, which calls it directly. That is why
  a one-hop expansion is sufficient here and a two-hop walk is not needed.

**What it does on SM-01:** `remove_quotes` enters the pack, at position 35 of 37, for
2 extra chunks and ~390 tokens (4854 → 5241, inside the 6000 budget).

**What it does on the golden set — nothing.** Same knob, same 42 pairs, one row apart:

| Config | R@5 | R@10 | P@5 | MRR | nDCG@10 | chunks | tokens | p95 |
|---|---|---|---|---|---|---|---|---|
| prefer-impl + parent exp. (shipped) | 0.786 | 0.905 | 0.233 | 0.593 | 0.652 | 34.5 | 6676 | 24.0 ms |
| + one-hop call expansion | 0.786 | 0.905 | 0.233 | 0.593 | 0.652 | 38.2 | 7464 | 21.9 ms |
| **delta** | **0.000** | **0.000** | **0.000** | **0.000** | **0.000** | +3.7 | **+11.8 %** | −2.1 ms |

Zero movement on every metric, for 11.8 % more tokens per query. That is not a
disappointing result, it is a **diagnosis of the golden set**: its 42 questions name the
symbol they are looking for, so the hop has nothing to bridge. The corpus that can
measure this feature does not exist yet — building it means writing questions in the
SM-01 shape, where the fix site is deliberately unnamed.

So it ships off, for the same reason the cross-encoder does (`descope-v1.md` §3) plus a
blunter one: the pack is embedded in the prompt, and the prompt is the replay cache key.
Turning it on re-records every fixture, which is a decision with a quota cost attached —
not a default. `tests/test_callgraph.py` asserts the default, because a silent flip is a
dead offline demo.

**Still true:** `docs/evaluation.md`'s 4/4 on `swe_mini` remains scoped to the repair
loop, because the benchmark still hands the agent the correct file. This feature makes
the end-to-end path *possible* on SM-01; it has not been run end to end, and until it is,
the honest claim is the one above and nothing more.
