"""The tool and filesystem policy — deterministic and pre-LLM (cahier §8.3).

No model is consulted here, and that is the point: §4/S puts it plainly — *a security
control you can negotiate with is not a security control*. Everything in this module
is a function of the arguments, so the same call always gets the same answer, and no
amount of retrieved text or clever prompting can move it.

**Whitelists, never blacklists.** The cahier asks the document to argue this rather
than assert it, so: a blacklist enumerates the attacks you thought of, and a path
blacklist in particular loses to encoding (``%2e%2e``), links (a symlink to ``/etc``),
case (``.GIT`` on a case-insensitive mount), and to anything simply not on the list.
The set of dangerous commands is unbounded and grows with every package installed;
the set of commands FORGE needs is five long and fits on a line. Only the second is
enumerable, so only the second is enumerated.

**realpath before every check**, never after. ``worktree/link`` pointing at
``/etc/shadow`` is inside the worktree by string comparison and outside it in every
sense that matters, and the resolution has to happen before the comparison for the
comparison to mean anything.

Denials of ``.git`` and ``.env`` are belt-and-braces on top of worktree confinement:
both live *inside* the tree, so worktree containment alone would permit rewriting git
history or reading credentials. They are a whitelist refinement, not a blacklist —
the rule is "the worktree except these", not "anything except these".
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path

from forge.guardrails.events import GuardrailLog, get_log
from forge.models import GuardrailAction, GuardrailStage

# The complete set of commands FORGE runs on model-written code. Everything the
# sandbox actually needs is here; anything else is a bug or an attack, and both
# deserve the same answer.
ALLOWED_COMMANDS: frozenset[str] = frozenset({"python", "python3", "pytest", "ruff", "git"})

# Subpaths refused even inside the worktree. `.git` because rewriting history is a
# way out of every other guarantee; `.env` and key material because reading them is
# the exfiltration this whole design exists to prevent.
DENIED_SUBPATHS: tuple[str, ...] = (".git", ".env", ".ssh", ".aws", ".netrc", "id_rsa")

# git is allowed as a *command* but not for arbitrary subcommands: `git push` is
# egress and `git config` can set a pager that executes. Only the read-only and
# apply verbs the change path needs.
ALLOWED_GIT_SUBCOMMANDS: frozenset[str] = frozenset(
    {"apply", "diff", "status", "show", "log", "rev-parse", "worktree", "branch", "add"}
)


@dataclass(frozen=True)
class Decision:
    """Allowed or not, and the rule id that decided it."""

    allowed: bool
    rule: str = ""
    detail: str = ""

    def __bool__(self) -> bool:
        return self.allowed


ALLOW = Decision(allowed=True)


def _denied_component(resolved: Path, root: Path) -> str | None:
    """The first denied component on the path *below the worktree root*.

    Scoped below the root deliberately: the worktree itself may sit under a directory
    called ``.git`` on some layouts (``.git/worktrees/...``), and refusing on the
    root's own components would make every path in such a tree unreachable.
    """
    try:
        relative = resolved.relative_to(root)
    except ValueError:
        return None
    for part in relative.parts:
        lowered = part.lower()
        if lowered in DENIED_SUBPATHS or lowered.startswith("id_rsa"):
            return part
    return None


def check_path(
    candidate: str | Path,
    workspace_root: str | Path,
    *,
    session_id: str = "",
    log: GuardrailLog | None = None,
    write: bool = True,
) -> Decision:
    """Is ``candidate`` a path FORGE may touch? Resolved first, then compared.

    Mirrors ``Workspace.resolve``'s guarantee and formalises it: that method raises
    for the editor's benefit, this one returns a decision and logs it, so a refusal
    becomes an auditable event rather than only an exception.
    """
    log = log or get_log()
    root = Path(os.path.realpath(str(workspace_root)))
    resolved = Path(os.path.realpath(str(candidate)))

    def refuse(rule: str, detail: str) -> Decision:
        log.emit(
            stage=GuardrailStage.POLICY,
            rule=rule,
            action=GuardrailAction.BLOCKED,
            session_id=session_id,
            detail=detail,
            target=str(candidate),
        )
        return Decision(allowed=False, rule=rule, detail=detail)

    if resolved != root and not str(resolved).startswith(str(root) + os.sep):
        return refuse("policy.path_escape", "resolves outside the session worktree")

    denied = _denied_component(resolved, root)
    if denied is not None:
        return refuse("policy.path_denied", f"{denied!r} is refused even inside the worktree")

    log.emit(
        stage=GuardrailStage.POLICY,
        rule="policy.path_allowed",
        action=GuardrailAction.ALLOWED,
        session_id=session_id,
        detail="write" if write else "read",
        target=str(candidate),
    )
    return ALLOW


# Global git options that take a value, so the value is not mistaken for the verb:
# `git -C /path apply` must resolve to "apply", not to "/path".
_GIT_OPTIONS_WITH_VALUE = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})


def _git_verb(argv: list[str]) -> str:
    """The subcommand in a git argv, skipping global options and their values."""
    skip_next = False
    for token in argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if token in _GIT_OPTIONS_WITH_VALUE:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        return token
    return ""


def check_command(
    command: list[str] | str,
    *,
    session_id: str = "",
    log: GuardrailLog | None = None,
) -> Decision:
    """Is this argv on the whitelist? Shell metacharacters are refused outright."""
    log = log or get_log()
    argv = shlex.split(command) if isinstance(command, str) else list(command)

    def refuse(rule: str, detail: str) -> Decision:
        log.emit(
            stage=GuardrailStage.POLICY,
            rule=rule,
            action=GuardrailAction.BLOCKED,
            session_id=session_id,
            detail=detail,
            target=" ".join(argv[:4]),
        )
        return Decision(allowed=False, rule=rule, detail=detail)

    if not argv:
        return refuse("policy.command_empty", "an empty command")

    # `python -c "..."` legitimately carries any character, so metacharacters are
    # checked on the *program* and its flags, not on a payload the sandbox contains.
    if any(character in argv[0] for character in ";|&$><`\n"):
        return refuse("policy.command_shell", "shell metacharacters in the program name")

    program = Path(argv[0]).name
    if program not in ALLOWED_COMMANDS:
        return refuse("policy.command_denied", f"{program!r} is not on the whitelist")

    if program == "git":
        verb = _git_verb(argv)
        if verb and verb not in ALLOWED_GIT_SUBCOMMANDS:
            return refuse("policy.git_subcommand", f"git {verb!r} is not permitted")

    log.emit(
        stage=GuardrailStage.POLICY,
        rule="policy.command_allowed",
        action=GuardrailAction.ALLOWED,
        session_id=session_id,
        target=program,
    )
    return ALLOW
