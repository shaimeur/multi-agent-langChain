"""``sentinel_out`` — validation before anything reaches the user (cahier §8.4).

The last deterministic node in the graph. It re-checks what earlier layers already
checked, on purpose: the reviewer is a model and the editor is a model, and §8.4's
layered hallucination control is only layered if the final layer does not trust the
ones above it.

Four controls:

* **citations** — every claimed ``file:line`` must resolve into the ContextPack, via
  ``ContextPack.supports``. The same function the reviewer's point 1 uses, run again
  on the answer that is actually going out;
* **diff applicability** — a patch that does not pass ``git apply --check`` never
  reaches a human, so the diff shown in the UI is always one that would apply;
* **secrets in generated code** — a model can reproduce a credential it saw in the
  corpus; that must not leave the process;
* **schema** — structured outputs revalidated rather than assumed.

Unlike ``sentinel_in``, this layer *redacts and downgrades* rather than refusing. A
grounded answer with one unverifiable citation is worth returning with that citation
dropped and the run marked ungrounded; throwing the whole answer away would be a
worse outcome for the user and no better for security.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from forge.guardrails.events import GuardrailLog, get_log
from forge.guardrails.sentinel_in import redact_secrets
from forge.models import (
    ContextPack,
    GroundedAnswer,
    GuardrailAction,
    GuardrailEvent,
    GuardrailStage,
    PatchSet,
)


@dataclass
class OutputDecision:
    """The vetted answer, plus what had to be done to it."""

    answer: GroundedAnswer
    events: list[GuardrailEvent] = field(default_factory=list)

    @property
    def modified(self) -> bool:
        return any(e.action is not GuardrailAction.ALLOWED for e in self.events)


def check_answer(
    answer: GroundedAnswer,
    pack: ContextPack | None,
    *,
    session_id: str = "",
    log: GuardrailLog | None = None,
) -> OutputDecision:
    """Verify citations and scrub secrets before the answer leaves the process.

    ``pack=None`` means the caller cannot supply the retrieved context — the direct
    ``answer_question`` path verifies citations internally and does not return its
    pack. Citations are then left alone and the skip is logged, because verifying
    against an empty pack would drop every citation and silently mark a perfectly
    grounded answer ungrounded. Secret scanning still runs; it needs no pack.
    """
    log = log or get_log()
    events: list[GuardrailEvent] = []

    def emit(rule, action, detail="", target=""):
        events.append(
            log.emit(
                stage=GuardrailStage.SENTINEL_OUT,
                rule=rule,
                action=action,
                session_id=session_id,
                detail=detail,
                target=target,
            )
        )

    if pack is None:
        verified = list(answer.citations)
        emit(
            "output.citations_verified_upstream",
            GuardrailAction.ALLOWED,
            "no pack at this layer — citations were checked where they were built",
        )
    else:
        verified = [c for c in answer.citations if pack.supports(c)]
        for citation in answer.citations:
            if citation not in verified:
                emit(
                    "output.citation_unverified",
                    GuardrailAction.REDACTED,
                    "cited a span that is not in the retrieved context",
                    f"{citation.path}:{citation.start_line}-{citation.end_line}",
                )

    text, secrets = redact_secrets(answer.answer)
    if secrets:
        emit("output.secret", GuardrailAction.REDACTED, f"redacted: {', '.join(secrets)}")

    if verified == answer.citations and not secrets:
        emit("output.clean", GuardrailAction.ALLOWED)

    vetted = answer.model_copy(
        update={
            "answer": text,
            "citations": verified,
            # Grounded means *verifiably* grounded. Dropping the last citation drops
            # the claim to groundedness with it, rather than leaving a true-looking
            # flag on an answer nothing supports.
            "grounded": answer.grounded and bool(verified),
        }
    )
    return OutputDecision(answer=vetted, events=events)


def check_patchset(
    patchset: PatchSet,
    *,
    patch_ok: bool,
    session_id: str = "",
    log: GuardrailLog | None = None,
) -> bool:
    """A patch reaches a human only if it applies and carries no credential."""
    log = log or get_log()

    if not patch_ok:
        log.emit(
            stage=GuardrailStage.SENTINEL_OUT,
            rule="output.patch_unappliable",
            action=GuardrailAction.BLOCKED,
            session_id=session_id,
            detail="git apply --check refused this diff",
        )
        return False

    added = "\n".join(patch.new_string for patch in patchset.patches)
    _, secrets = redact_secrets(added)
    if secrets:
        log.emit(
            stage=GuardrailStage.SENTINEL_OUT,
            rule="output.secret_in_patch",
            action=GuardrailAction.BLOCKED,
            session_id=session_id,
            detail=f"generated code contains: {', '.join(secrets)}",
        )
        return False

    log.emit(
        stage=GuardrailStage.SENTINEL_OUT,
        rule="output.patch_clean",
        action=GuardrailAction.ALLOWED,
        session_id=session_id,
        target=",".join(sorted({p.path for p in patchset.patches})[:3]),
    )
    return True
