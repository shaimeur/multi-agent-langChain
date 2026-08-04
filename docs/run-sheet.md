# Run sheet — the 4-minute demo

One page, for the second screen. Beats are §15.6 / slide 10. Every command is copy-paste.

---

## T-30 min · pre-flight (do this once, before recording)

```bash
# 1. THE ONE-LINE BLOCKER — without it every offline `forge ask` raises FixtureMiss.
#    The recorded answers are keyed on the model id, and .env pins the wrong one.
sed -i 's/^GEMINI_REASONER_MODEL=.*/GEMINI_REASONER_MODEL=gemini-flash-latest/' .env

# 2. Pre-flight: full re-index + prove offline replay. Must print "ready".
scripts/stage_demo.sh warm

# 3. The stack, the way the examiner will run it.
docker compose up --build          # → http://localhost:8000

# 4. THE TRAP. `stage_demo.sh warm` indexes the EMBEDDED store (data/qdrant) that the CLI
#    uses. The container has its OWN Qdrant, in the `qdrant-storage` volume, and step 2
#    does not touch it. On this machine that volume still held an index of FORGE's own
#    source from an earlier session — so the browser demo cited src/forge/... instead of
#    sqlparse. Click ⟳ Rebuild index in the UI (it does a FULL rebuild since D15b), or:
curl -s -X POST localhost:8000/v1/index -H 'content-type: application/json' \
     -d '{"path":"data/target","incremental":false}'
sleep 120 && curl -s localhost:6333/collections/code | grep -o '"points_count":[0-9]*'
#    Must be 617. Anything else and you are about to demo the wrong repository.
```

If step 2 fails it tells you which of the two causes it is. Do not start recording until it
prints `ready` **and** step 4 says 617.

> **The button used to be the trap.** Before D15b it sent `incremental: true` — "index
> what `git diff` says changed" — so on an unchanged HEAD it returned `202 accepted` and
> did nothing at all. It now rebuilds properly. The clean-machine test never caught it,
> because that runs under a fresh compose project with empty volumes.

> ⚠️ **The new footgun, and it is right next to the demo.** The sidebar has a target
> repository picker. Switching does *not* touch the index — but selecting a different repo
> and then clicking **Rebuild index** replaces the sqlparse index with that repo's, and the
> replay fixtures go with it. If you demo the picker, switch **back to `target` before
> rebuilding anything**. `scripts/stage_demo.sh warm` puts it right either way.

---

## Which mode to record in

| Beat | Live model | `CACHE_MODE=replay` |
|---|---|---|
| 1–2 · index + grounded question | ✅ | ✅ **proven** — use the exact wording below |
| 3–5 · bug report → plan → tests → patch | ✅ **proven 03/08 in the browser** | ⚠️ **not proven end to end** |
| 5 · poisoned comment + guardrail | ✅ | ✅ deterministic, no model needed |
| 6 · cost panel | ✅ | ✅ |

**Record with the live model.** That is the path proven in a browser on 03/08, and the
recording *is* the fallback — it does not itself need to be offline. Budget it: the free
tier is **≈20 requests/day per model id**, one full run is ~11, so you get **one clean take
plus one retry**. Rehearse the clicks with the stack up *before* you start spending calls.

`CACHE_MODE=replay` is what you show the jury live tomorrow if the wifi dies — and for the
Q&A beat it is proven. Say that out loud; it is a stronger claim than pretending the whole
run is offline.

---

## The beats

**1 · Index** — `Index repo` button, or `uv run forge index data/target --full`.
Say: *617 chunks, 59 files, AST boundaries — not 500-character windows.*

**2 · Grounded question** — this exact wording (it has a committed fixture):

> `How does the lexer tokenize SQL statements?`

Point at the badge: **● grounded**, and the citation table. Say: *the citations are verified
in code against what was actually retrieved — the model's word is never the authority.*

**3 · Bug report** — switch the toggle to **Change**, paste:

> `Quoted identifiers come back wrong: parsing 'select * from "my table"' and asking the identifier for its real name returns the name with a trailing double quote still attached. It should be just: my table`

Point at the **agent timeline** as it fills. Say: *six agents, and this is the visual proof
it is not one agent in a loop.*

**4 · Plan gate** → approve. Then the tester writes the **failing** test first — point at
`exit=1`. Say: *red before green, so a patch that changes nothing cannot pass.*
Then the **patch gate** with the coloured diff → approve. Say: *nothing has touched disk
until this click.*

**5 · The poisoned comment** — in a second terminal:

```bash
scripts/stage_demo.sh plant
uv run forge index data/target --full     # make it retrievable
```

Ask any question that hits `lexer.py`. The **guardrail panel opens by itself**:
`[REDACTED] injection.override`. Say: *neutralised before the planner saw it, and the code
around it survived, so the task still completes.*

**6 · Cost panel** — turns, calls, tokens, guardrail events, latency.

**Optional 7 · the target picker** (D15b, and a good security answer). Open the sidebar
dropdown, switch to another repository, point at the banner: *the index is still the old
one — rebuild it.* Then say the part that matters:

> *"It is a select, not a text box. The server enumerates what may be chosen and re-checks
> the value it gets back. That is deliberate: the target repo is the confinement root for
> the file-reading tools, so a free-text path here would let the browser choose what the
> sandbox may read."*

Then **switch back to `target`** before touching Rebuild.

---

## The spare beat — free, offline, re-runnable in front of them

If a beat dies, or the jury asks for evidence rather than a demo, run this. It spends **no
quota**, needs **no network**, and finishes in ~25 seconds:

```bash
QDRANT_URL= CACHE_MODE=replay uv run python evals/run_swe_mini.py
```

→ `4/4 repaired`, 0 regressions. Verified again today, exit 0.

Say the caveat before they ask it: *the harness hands the agent the correct file, so this
scores the repair loop, not retrieval end to end.* It is in `evaluation.md` under "three
things this number does not say" — owning it is worth more than the 4/4.

The security suite is the same kind of asset — instant, offline, quotable:

```bash
CACHE_MODE=replay uv run pytest evals/security -q     # → 32/32 attacks mitigated
uv run python scripts/mcp_smoke.py                    # → C6/MCP PASS, if C6 comes up
```

---

## Recovery — if a beat dies mid-take

| Symptom | Do this, keep talking |
|---|---|
| `429` quota mid-run | Say *"that is the free tier, ~20 requests a day — it is in the risk register"*, switch to the recorded video |
| `FixtureMiss` | You are in replay with a wording that was never recorded. Switch to `CACHE_MODE=auto`, or use the exact question above |
| Sandbox unavailable | It degrades to the documented `subprocess` fallback; every report records which ran. `limitations.md` §1 |
| Docker broken | **Do not debug it.** Play the video. This is the D15 rule |

---

## T-0 · after the recording

```bash
scripts/stage_demo.sh clean        # un-plant the comment, restore the pinned tree
git -C data/target status --short  # must be empty
```

Then: three stopwatch rehearsals, and say the four jury answers out loud — they are drafted
in the `docs/slides.md` annexe, but reading them is not the same as having said them.
