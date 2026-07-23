---
description: Verify progress against real command output, rewrite docs/STATE.md, and commit the checkpoint.
---

Refresh the project's continuity state so the next session resumes cleanly. Work from real command
output, never from recollection.

1. Run `git status --short` and `git log --oneline -5`. Use what they actually print.
2. For every item you intend to promote to **Done**, re-run its Definition-of-Done command and record
   the exit status. If it cannot be run, or does not pass, it stays under **In progress** with the
   reason. Never mark a DoD done on belief — `? unverified` is an honest status; a false `[x]` is not.
3. Rewrite `docs/STATE.md` in full (rewrite in place, do not append):
   - Update `Last updated`, `Roadmap day`, `Branch`, `Last commit`.
   - Move verified items to **Done (verified)** as ``- [x] D<day> <what> — `<command>` → <result>``.
   - Under **In progress**, name the *next concrete action* specifically enough to start without
     re-reading anything. Not "continue the RAG work" — instead "add the reranker in
     `src/forge/rag/rerank.py`, then re-run `uv run python evals/run_retrieval.py`".
   - Move unresolved items to **Blocked / open decisions**.
   - Add anything a fresh session would waste time re-investigating to **Do not redo**.
   - Keep it under 80 lines by deleting stale entries.
4. Tick the matching boxes in `GOALS.md` — only boxes whose DoD command actually passed — and update
   its pointer to STATE.md if the wording drifted. Do not restructure the plan or the descope register.
5. Commit with `chore(state): checkpoint D<day> — <summary>`.

Do not touch `docs/cahier-des-charges.md` or `docs/descope-v1.md`; a hook blocks them and they are the
human's to edit.
