"""Ephemeral, hardened execution of untrusted code — cahier §8.3.

This is the module that assumes the code it runs is hostile. It was not written by a
person, it was written by a model working from a retrieved context, and the honest
threat model is *arbitrary code execution on the developer's laptop*. Nothing here
sanitises the code; the containment is structural.

**The container backend** starts one throwaway container per run:

===========================  ==========================================================
``network_mode=none``        no egress — a patch cannot exfiltrate the repo or the .env
``read_only=True``           the image is immutable at run time; only /work can be written
``volumes={worktree: /work}``the session worktree is the *only* host path in the container
``tmpfs /tmp``               noexec, nosuid, size-capped — pytest needs a scratch dir
``cap_drop=ALL``             no CAP_NET_RAW, no CAP_SYS_ADMIN, nothing
``no-new-privileges``        a setuid binary cannot escalate back out
``user=<worktree owner>``    never uid 0
``mem_limit == memswap``     a 512 MB cap that swap cannot be used to walk around
``pids_limit``               what actually contains a fork bomb
``init=True``                pid 1 reaps, so a killed tree leaves no zombies
``log_config max-size``      10 GB of stdout cannot fill the host disk
===========================  ==========================================================

**The fallback backend** runs the command as a subprocess with ``setrlimit`` and a
process-group kill when the Docker socket is absent. It is *materially weaker* — no
network isolation, no filesystem isolation — and its gaps are written down in
docs/limitations.md §1 rather than papered over. Which one ran is recorded on every
``ExecutionReport``, so no result can quietly claim more confinement than it had.

Neither backend inherits this process's environment. That is deliberate: the parent
holds ``GOOGLE_API_KEY`` and ``GROQ_API_KEY``, and handing those to code a model just
wrote would make the whole exercise pointless.
"""

from __future__ import annotations

import contextlib
import os
import resource
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from forge.config import SandboxBackend, Settings, get_settings
from forge.models import Isolation

_CONTAINER_WORKDIR = "/work"

# Bounds the host's json-file log for one container: 2 x 8 MB. Well above any real
# test transcript and far below "fills the disk" — the runner only ever reads
# ``sandbox_max_output_bytes`` of it, this is purely a cap on what Docker stores.
# Two files rather than one so a rotation mid-run cannot leave the log momentarily
# empty while the runner is reading it.
_LOG_MAX_SIZE = "8m"
_LOG_MAX_FILES = "2"

# The fallback writes output to files rather than pipes so a runaway printer cannot
# be read into this process's memory. RLIMIT_FSIZE then bounds those files; it also
# bounds any file the tests themselves write, so it is generous rather than tight.
_FALLBACK_FSIZE_BYTES = 64 * 1024 * 1024


class SandboxUnavailable(RuntimeError):
    """No backend could run the command — a missing socket under ``DOCKER``, or a
    missing image. Distinct from a failing command, which is a normal result."""


@dataclass(frozen=True)
class RawResult:
    """One raw execution, before anything is parsed out of it.

    ``exit_code is None`` means the command never exited on its own — it was killed
    at the deadline. Callers map that to ``ExecutionOutcome.TIMEOUT``.
    """

    exit_code: int | None
    stdout: str
    stderr: str
    duration_s: float
    isolation: Isolation
    truncated: bool = False


# --- backend selection ----------------------------------------------------

_client_cache: dict = {}


def _docker_client():
    """A pinged Docker client, or None. Cached — including the negative answer, so a
    machine without a socket does not pay a connection timeout on every run."""
    if "client" not in _client_cache:
        client = None
        try:
            import docker

            candidate = docker.from_env(timeout=10)
            candidate.ping()
            client = candidate
        except Exception:
            client = None
        _client_cache["client"] = client
    return _client_cache["client"]


def reset_docker_client() -> None:
    """Drop the cached client. For tests that swap the environment underneath it."""
    _client_cache.clear()


def docker_available() -> bool:
    return _docker_client() is not None


def active_isolation(settings: Settings | None = None) -> Isolation:
    """The backend a run would get right now, without running anything."""
    settings = settings or get_settings()
    if settings.sandbox_backend is SandboxBackend.SUBPROCESS:
        return Isolation.SUBPROCESS
    if settings.sandbox_backend is SandboxBackend.DOCKER:
        return Isolation.DOCKER
    return Isolation.DOCKER if docker_available() else Isolation.SUBPROCESS


def run_in_sandbox(
    command: list[str],
    workdir: str | Path,
    *,
    settings: Settings | None = None,
    timeout_s: int | None = None,
) -> RawResult:
    """Run ``command`` with ``workdir`` as its only writable path.

    ``workdir`` is a session worktree (``core/workspace.py``); it is resolved here so
    a relative or symlinked path cannot widen what gets mounted.
    """
    settings = settings or get_settings()
    timeout_s = timeout_s or settings.sandbox_timeout_s
    workdir = Path(workdir).resolve()
    if not workdir.is_dir():
        raise SandboxUnavailable(f"workdir does not exist: {workdir}")

    backend = settings.sandbox_backend
    client = None if backend is SandboxBackend.SUBPROCESS else _docker_client()

    if client is not None:
        return _run_docker(client, command, workdir, settings, timeout_s)
    if backend is SandboxBackend.DOCKER:
        raise SandboxUnavailable(
            "SANDBOX_BACKEND=docker but the Docker socket did not answer. "
            "Start Docker, or set SANDBOX_BACKEND=subprocess and accept the gaps "
            "documented in docs/limitations.md §1."
        )
    return _run_subprocess(command, workdir, settings, timeout_s)


# --- container backend ----------------------------------------------------


def _sandbox_uid(workdir: Path) -> str:
    """Run as whoever owns the worktree, so the mount is writable — but never root.

    A root-owned worktree is not something FORGE creates, and honouring it would
    hand uid 0 to model-written code; 1000 (the image's `sandbox` user) is the safe
    answer even though writes would then fail loudly.
    """
    info = workdir.stat()
    return f"{info.st_uid}:{info.st_gid}" if info.st_uid != 0 else "1000:1000"


def _read_capped(container, *, stdout: bool, cap: int) -> tuple[str, bool]:
    """Read at most ``cap`` bytes of one stream, then stop.

    Streamed and cut at the head rather than tailed: tailing a 10 GB transcript means
    *pulling* 10 GB through the socket, which is the denial of service this is meant
    to survive. The cost is that a truncated run loses pytest's trailing summary
    line — acceptable, because the exit code carries the verdict and the counts are
    only detail (``report.py``).
    """
    chunks: list[bytes] = []
    size = 0
    truncated = False
    stream = container.logs(stdout=stdout, stderr=not stdout, stream=True, follow=False)
    try:
        for chunk in stream:
            chunks.append(chunk)
            size += len(chunk)
            if size >= cap:
                truncated = True
                break
    finally:
        with contextlib.suppress(Exception):
            stream.close()
    text = b"".join(chunks)[:cap].decode("utf-8", errors="replace")
    return text, truncated


def _run_docker(client, command, workdir, settings: Settings, timeout_s: int) -> RawResult:
    from docker.types import LogConfig

    started = time.monotonic()
    container = client.containers.create(
        image=settings.sandbox_image,
        command=command,
        working_dir=_CONTAINER_WORKDIR,
        # --- isolation ---
        network_mode="none",
        network_disabled=True,
        read_only=True,
        user=_sandbox_uid(workdir),
        cap_drop=["ALL"],
        security_opt=["no-new-privileges"],
        volumes={str(workdir): {"bind": _CONTAINER_WORKDIR, "mode": "rw"}},
        tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"},
        # --- resource caps ---
        # memswap == mem is the important half: without it the container can swap
        # its way past the memory cap and take the host down with it.
        mem_limit=f"{settings.sandbox_memory_mb}m",
        memswap_limit=f"{settings.sandbox_memory_mb}m",
        nano_cpus=int(settings.sandbox_cpus * 1_000_000_000),
        pids_limit=settings.sandbox_pids_limit,
        init=True,
        log_config=LogConfig(
            type=LogConfig.types.JSON,
            config={"max-size": _LOG_MAX_SIZE, "max-file": _LOG_MAX_FILES},
        ),
        # A minimal environment, never this process's. HOME and the caches point at
        # the tmpfs because the root filesystem is read-only.
        environment={
            "HOME": "/tmp",
            "XDG_CACHE_HOME": "/tmp",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "PATH": "/usr/local/bin:/usr/bin:/bin",
        },
    )

    exit_code: int | None = None
    try:
        container.start()
        try:
            exit_code = int(container.wait(timeout=timeout_s).get("StatusCode", -1))
        except Exception:
            # Either the deadline passed or the daemon hiccuped mid-wait. Ask the
            # container which: one that already exited has a real verdict to report,
            # and only one still running is a genuine timeout.
            with contextlib.suppress(Exception):
                container.reload()
                if container.status != "running":
                    exit_code = int(container.attrs["State"]["ExitCode"])
            if exit_code is None:
                # SIGKILL, not SIGTERM: an infinite loop does not get a chance to
                # trap the signal and keep running.
                with contextlib.suppress(Exception):
                    container.kill()

        cap = settings.sandbox_max_output_bytes
        stdout, out_cut = _read_capped(container, stdout=True, cap=cap)
        stderr, err_cut = _read_capped(container, stdout=False, cap=cap)
    finally:
        with contextlib.suppress(Exception):
            container.remove(force=True, v=True)

    return RawResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_s=time.monotonic() - started,
        isolation=Isolation.DOCKER,
        truncated=out_cut or err_cut,
    )


# --- subprocess fallback (docs/limitations.md §1) -------------------------


def _fallback_env(workdir: Path) -> dict[str, str]:
    """A minimal environment. Emphatically not ``os.environ`` — the parent holds the
    provider API keys, and the point of a sandbox is that the code cannot read them.

    PATH has to include this interpreter's directory, because the fallback has no
    image to get ``pytest`` from and must borrow the development venv's — itself one
    of the reasons this backend is a fallback and not the design.
    """
    return {
        "PATH": f"{Path(sys.executable).parent}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(workdir),
        "TMPDIR": str(workdir),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "LANG": "C.UTF-8",
    }


def _apply_rlimits(settings: Settings, timeout_s: int):
    """The child-side limits, applied between fork and exec.

    **RLIMIT_DATA, not RLIMIT_AS**, for the memory cap. RLIMIT_AS bounds reserved
    *address space*, which a threaded Rust or Go binary reserves far more of than it
    ever touches: measured here, ruff crashed with SIGABRT in 8 of 10 identical runs
    under a 512 MB RLIMIT_AS, and the outcome moved with unrelated environment
    changes. A sandbox that fails nondeterministically is worse than one that is
    merely weak — the repair loop would spend its budget on phantom crashes. Under
    RLIMIT_DATA the same command passed 10 of 10, and a deliberate 1 GB allocation is
    still refused, which is what the cap is actually for. (RLIMIT_DATA only covers
    mmap on Linux >= 4.7; see docs/limitations.md §1.)

    Deliberately *no* RLIMIT_NPROC: it caps processes per real uid, not per tree, so
    on a busy desktop it either fires immediately on the developer's own process
    count or fails to bound the tree at all. Fork-bomb containment in this backend is
    the process-group kill and the CPU limit; the pid cap is the container's job.
    """

    def child() -> None:
        memory = settings.sandbox_memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_DATA, (memory, memory))
        # A hard CPU ceiling above the wall-clock deadline: the killpg is the primary
        # stop, this catches a child that somehow outlives its group.
        resource.setrlimit(resource.RLIMIT_CPU, (timeout_s + 1, timeout_s + 2))
        resource.setrlimit(resource.RLIMIT_FSIZE, (_FALLBACK_FSIZE_BYTES,) * 2)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return child


def _head(path: Path, cap: int) -> tuple[str, bool]:
    """The first ``cap`` bytes of a capture file, and whether there was more."""
    with path.open("rb") as handle:
        data = handle.read(cap)
        truncated = bool(handle.read(1))
    return data.decode("utf-8", errors="replace"), truncated


def _run_subprocess(command, workdir: Path, settings: Settings, timeout_s: int) -> RawResult:
    started = time.monotonic()
    cap = settings.sandbox_max_output_bytes

    with tempfile.TemporaryDirectory(prefix="forge-sandbox-") as capture:
        out_path = Path(capture) / "stdout"
        err_path = Path(capture) / "stderr"
        exit_code: int | None = None

        with out_path.open("wb") as out_file, err_path.open("wb") as err_file:
            try:
                process = subprocess.Popen(
                    command,
                    cwd=str(workdir),
                    stdout=out_file,
                    stderr=err_file,
                    stdin=subprocess.DEVNULL,
                    env=_fallback_env(workdir),
                    # Its own session, so the deadline can kill the whole tree rather
                    # than just the process that happens to hold the pid.
                    start_new_session=True,
                    preexec_fn=_apply_rlimits(settings, timeout_s),  # noqa: PLW1509
                )
            except (OSError, ValueError) as error:
                raise SandboxUnavailable(f"could not start {command[0]!r}: {error}") from error

            try:
                exit_code = process.wait(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                _kill_group(process)
                exit_code = None

        stdout, out_cut = _head(out_path, cap)
        stderr, err_cut = _head(err_path, cap)

    return RawResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_s=time.monotonic() - started,
        isolation=Isolation.SUBPROCESS,
        truncated=out_cut or err_cut,
    )


def _kill_group(process: subprocess.Popen) -> None:
    """SIGKILL the whole process group, then reap. Killing the pid alone would leave
    a forked child holding the CPU with nothing watching it."""
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(os.getpgid(process.pid), 9)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=5)
