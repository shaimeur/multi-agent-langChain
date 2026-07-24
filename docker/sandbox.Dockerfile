# syntax=docker/dockerfile:1
# The execution sandbox — cahier §8.3. Every command FORGE runs on model-written
# code runs in an ephemeral container built from this image, one per run, with
# --network=none and a read-only root filesystem.
#
# Deliberately *not* the application image. `Dockerfile` carries torch, the Qdrant
# client and the provider SDKs with their credentials; none of that should be
# reachable from code a model just wrote. This image holds a test runner, a linter,
# and nothing else — the smaller the image, the smaller the surface a hostile patch
# has to work with. No curl, no git, no compiler, no package manager at run time.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # The root filesystem is read-only at run time, so anything wanting a home or a
    # cache must land on the tmpfs the runner mounts at /tmp. Without this, pytest's
    # first attempt to write a cache directory kills the run.
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp

# Pinned: a sandbox that silently upgrades its test runner silently changes what
# "the tests passed" means. Moving these is a deliberate act, not a rebuild
# side effect. pip itself is removed afterwards — nothing installs at run time.
RUN pip install --no-cache-dir \
        pytest==8.3.4 \
        pytest-cov==6.0.0 \
        ruff==0.9.6 \
    && pip uninstall -y pip setuptools wheel 2>/dev/null || true

# uid 1000, matching the runner's `user=` flag. Non-root is the second line of
# defence behind cap_drop=ALL: even given a container escape, this uid owns nothing
# on the host. --no-create-home because HOME points at the tmpfs instead.
RUN useradd --no-create-home --uid 1000 sandbox

# /work is the session worktree, bind-mounted read-write at run time. It is the only
# writable path in the container that survives the run.
WORKDIR /work
USER sandbox

# No useful default command. The runner always passes an explicit argv, and a
# sandbox that does something when invoked bare is a sandbox that can be invoked
# bare. Fail loudly instead of falling back to a shell.
CMD ["python", "-c", "raise SystemExit('forge-sandbox: no command given')"]
