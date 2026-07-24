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

<!-- Entries below this line are not sandbox-related. Add new ones with the same
     shape: where, since which day, and the gap stated plainly. -->
