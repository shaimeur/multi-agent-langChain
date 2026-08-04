// Presentational components for the FORGE UI. All state lives in App; these render.
import { useEffect, useRef, useState } from 'react'
import type {
  ApprovalPayload,
  CitationView,
  GuardrailEventView,
  PlanPayload,
  RepoOption,
  SessionInfo,
  SessionMetrics,
} from './api'

// One label + colour per graph node, so the timeline reads as agents, not function names.
const NODE_META: Record<string, { label: string; color: string }> = {
  supervisor: { label: 'Supervisor', color: 'text-slate-300' },
  retriever: { label: 'Retriever', color: 'text-sky-300' },
  planner: { label: 'Planner', color: 'text-violet-300' },
  plan_approval: { label: 'Plan gate', color: 'text-amber-300' },
  regression: { label: 'Test author', color: 'text-teal-300' },
  editor: { label: 'Editor', color: 'text-emerald-300' },
  patch_approval: { label: 'Patch gate', color: 'text-amber-300' },
  apply: { label: 'Apply', color: 'text-emerald-400' },
  verify: { label: 'Sandbox', color: 'text-teal-300' },
  reviewer: { label: 'Reviewer', color: 'text-fuchsia-300' },
  answer: { label: 'Answerer', color: 'text-sky-300' },
  summary: { label: 'Memory', color: 'text-slate-400' },
}

const nodeMeta = (node: string) =>
  NODE_META[node] ?? { label: node, color: 'text-slate-300' }

const short = (id: string) => (id.length > 14 ? `${id.slice(0, 14)}…` : id)

// --- Sidebar ---------------------------------------------------------------

export function Sidebar(props: {
  sessions: SessionInfo[]
  activeId: string | null
  busy: boolean
  repos: RepoOption[]
  onSelect: (id: string) => void
  onNew: () => void
  onDelete: (id: string) => void
  onIndex: () => void
  onPickRepo: (path: string) => void
}) {
  const current = props.repos.find((r) => r.is_current)
  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-slate-800 bg-[#0d1117]">
      <div className="flex items-center gap-2 px-4 py-4">
        <span className="text-lg font-semibold tracking-tight text-cyan-300">◆ FORGE</span>
      </div>
      <button
        onClick={props.onNew}
        disabled={props.busy}
        className="mx-3 mb-3 rounded-md border border-cyan-800 bg-cyan-950/40 px-3 py-2 text-sm font-medium text-cyan-200 hover:bg-cyan-900/40 disabled:opacity-50"
      >
        + New session
      </button>

      {/* D15b — a *select*, never a text field. The server enumerates what may be
          chosen and re-checks the value it gets back, because target_repo is the
          confinement root for the file tools: a free-text path here would let the
          browser choose what the sandbox may read (§8.3). */}
      {props.repos.length > 0 && (
        <div className="mx-3 mb-2">
          <label
            htmlFor="repo-picker"
            className="mb-1 block text-[10px] uppercase tracking-wider text-slate-500"
          >
            Target repository
          </label>
          <select
            id="repo-picker"
            value={current?.path ?? ''}
            disabled={props.busy}
            onChange={(e) => props.onPickRepo(e.target.value)}
            className="w-full rounded-md border border-slate-700 bg-slate-900/60 px-2 py-1.5 text-xs text-slate-300 disabled:opacity-50"
          >
            {props.repos.map((r) => (
              <option key={r.path} value={r.path}>
                {r.name}
                {r.is_git ? '' : ' (no git — ask only)'}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* §15.6 opens on `forge index`. The demo runs pre-warmed, but the DoD is "no
          terminal", so the browser has to be able to kick one off. 202 — fire and forget.
          Full rebuild, not incremental: see the note on api.startIndex. */}
      <button
        onClick={props.onIndex}
        disabled={props.busy}
        title="Rebuild the index for the target repo from scratch (runs in the background)"
        className="mx-3 mb-3 rounded-md border border-slate-700 px-3 py-1.5 text-xs text-slate-400 hover:bg-slate-800/60 disabled:opacity-50"
      >
        ⟳ Rebuild index
      </button>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-3">
        {props.sessions.length === 0 && (
          <p className="px-2 py-3 text-xs text-slate-500">No sessions yet.</p>
        )}
        {props.sessions.map((s) => (
          <div
            key={s.session_id}
            className={`group flex cursor-pointer items-center justify-between rounded-md px-2 py-2 text-sm ${
              s.session_id === props.activeId
                ? 'bg-slate-800 text-slate-100'
                : 'text-slate-400 hover:bg-slate-800/50'
            }`}
            onClick={() => props.onSelect(s.session_id)}
          >
            <div className="min-w-0">
              <div className="truncate font-mono text-xs">{short(s.session_id)}</div>
              <div className="truncate text-[10px] text-slate-500">{s.branch}</div>
            </div>
            <button
              onClick={(e) => {
                e.stopPropagation()
                props.onDelete(s.session_id)
              }}
              className="ml-2 hidden text-slate-500 hover:text-red-400 group-hover:block"
              title="Close session"
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    </aside>
  )
}

// --- Agent timeline --------------------------------------------------------

export interface TimelineItem {
  id: number
  node: string
  detail: string
}

export function Timeline({ items, running }: { items: TimelineItem[]; running: boolean }) {
  return (
    <div className="flex h-full flex-col">
      <h2 className="border-b border-slate-800 px-4 py-3 text-xs font-semibold uppercase tracking-wider text-slate-400">
        Agent activity
      </h2>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {items.length === 0 && (
          <p className="text-xs text-slate-500">The graph timeline shows here as agents run.</p>
        )}
        <ol className="space-y-2">
          {items.map((it) => {
            const m = nodeMeta(it.node)
            return (
              <li key={it.id} className="flex items-start gap-2 text-sm">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-current opacity-70" />
                <span className={`font-medium ${m.color}`}>{m.label}</span>
                {it.detail && <span className="text-slate-500">— {it.detail}</span>}
              </li>
            )
          })}
        </ol>
        {running && <p className="mt-3 animate-pulse text-xs text-cyan-400">running…</p>}
      </div>
    </div>
  )
}

// --- Diff viewer -----------------------------------------------------------

export function DiffViewer({ diff }: { diff: string }) {
  const lineClass = (line: string) => {
    if (line.startsWith('+') && !line.startsWith('+++')) return 'bg-emerald-950/40 text-emerald-300'
    if (line.startsWith('-') && !line.startsWith('---')) return 'bg-red-950/40 text-red-300'
    if (line.startsWith('@@')) return 'text-cyan-400'
    if (line.startsWith('diff ') || line.startsWith('+++') || line.startsWith('---'))
      return 'text-slate-500'
    return 'text-slate-400'
  }
  return (
    <pre className="overflow-x-auto rounded-md border border-slate-800 bg-[#0b0f14] p-3 text-xs leading-relaxed">
      {diff.split('\n').map((line, i) => (
        <div key={i} className={`whitespace-pre ${lineClass(line)}`}>
          {line || ' '}
        </div>
      ))}
    </pre>
  )
}

// --- Approval modal (plan gate + patch gate) -------------------------------

export function ApprovalModal(props: {
  payload: ApprovalPayload
  busy: boolean
  onApprove: () => void
  onReject: () => void
}) {
  const p = props.payload
  const isPlanGate = p.kind === 'plan_approval'
  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-black/60 p-4">
      <div className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-xl border border-amber-700/50 bg-[#0d1117] shadow-2xl">
        <div className="flex items-center gap-2 border-b border-slate-800 px-5 py-3">
          <span className="text-amber-300">⏸</span>
          <h2 className="font-semibold text-slate-100">
            {isPlanGate ? 'Approve the plan' : 'Approve the patch'}
          </h2>
          <span className="ml-auto text-xs text-slate-500">human control point · §5.5</span>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          {p.kind === 'plan_approval' ? (
            <PlanBody payload={p} />
          ) : (
            <DiffViewer diff={p.diff ?? '(no diff in payload)'} />
          )}
        </div>
        <div className="flex justify-end gap-2 border-t border-slate-800 px-5 py-3">
          <button
            onClick={props.onReject}
            disabled={props.busy}
            className="rounded-md border border-slate-700 px-4 py-2 text-sm text-slate-300 hover:bg-slate-800 disabled:opacity-50"
          >
            Reject
          </button>
          <button
            onClick={props.onApprove}
            disabled={props.busy}
            className="rounded-md bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            Approve
          </button>
        </div>
      </div>
    </div>
  )
}

function PlanBody({ payload }: { payload: PlanPayload }) {
  return (
    <div className="space-y-4">
      <p className="text-sm text-slate-300">{payload.summary}</p>
      <div className="space-y-2">
        {payload.steps.map((s, i) => (
          <div key={i} className="rounded-md border border-slate-800 bg-slate-900/40 p-3">
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-sm font-medium text-slate-100">{s.intent}</span>
              <code className="shrink-0 rounded bg-slate-800 px-1.5 py-0.5 text-[11px] text-cyan-300">
                {s.target_path}
              </code>
            </div>
            {s.rationale && <p className="mt-1 text-xs text-slate-400">{s.rationale}</p>}
          </div>
        ))}
      </div>
      {(payload.blast_radius?.length ?? 0) > 0 && (
        <p className="text-xs text-slate-500">
          Blast radius: {payload.blast_radius.join(', ')}
        </p>
      )}
    </div>
  )
}

// --- Citations panel (cahier §15.6: "citations ancrées affichées") ---------

export function CitationsPanel(props: { citations: CitationView[]; grounded: boolean | null }) {
  const { citations, grounded } = props
  if (grounded === null && citations.length === 0) {
    return (
      <p className="text-xs text-slate-500">
        Ask a question about the codebase — the spans the answer is grounded in show here.
      </p>
    )
  }
  return (
    <div className="space-y-3">
      {/* `grounded` is the server's verified flag, not a guess: sentinel_out checks every
          citation against the retrieved pack, so false means "unsupported", not "none found". */}
      <div
        className={`rounded-md border p-2 text-xs ${
          grounded
            ? 'border-emerald-800/60 bg-emerald-950/30 text-emerald-300'
            : 'border-amber-800/60 bg-amber-950/30 text-amber-300'
        }`}
      >
        {grounded ? '● grounded' : '○ ungrounded'} — {citations.length} verified citation
        {citations.length === 1 ? '' : 's'}
      </div>
      <ul className="space-y-1.5">
        {citations.map((c, i) => (
          <li
            key={`${c.path}:${c.start_line}:${i}`}
            className="rounded-md border border-slate-800 bg-slate-900/40 px-2 py-1.5"
          >
            <code className="break-all text-[11px] text-cyan-300">
              {c.path}:{c.start_line}
              {c.end_line !== c.start_line && `-${c.end_line}`}
            </code>
          </li>
        ))}
      </ul>
    </div>
  )
}

// --- Guardrail panel (cahier §8.5 — the queryable event log) ---------------

const ACTION_STYLE: Record<string, string> = {
  blocked: 'border-red-800/60 bg-red-950/30 text-red-300',
  redacted: 'border-amber-800/60 bg-amber-950/30 text-amber-300',
  flagged: 'border-yellow-800/60 bg-yellow-950/30 text-yellow-300',
  allowed: 'border-slate-800 bg-slate-900/40 text-slate-500',
}

const isFiring = (e: GuardrailEventView) => e.action !== 'allowed'

export function GuardrailsPanel({ events }: { events: GuardrailEventView[] }) {
  if (events.length === 0) {
    return (
      <p className="text-xs text-slate-500">
        No guardrail events for this session yet. Every decision is logged here — allowed
        included, so a clean run still proves the checks ran.
      </p>
    )
  }
  return (
    <ul className="space-y-2">
      {events.map((e, i) => (
        <li
          key={`${e.created_at}-${e.rule}-${i}`}
          className={`rounded-md border p-2 text-xs ${ACTION_STYLE[e.action] ?? ACTION_STYLE.allowed}`}
        >
          <div className="flex items-baseline gap-2">
            <span className="font-semibold uppercase tracking-wide">{e.action}</span>
            <code className="text-[11px] opacity-80">{e.rule}</code>
            <span className="ml-auto shrink-0 text-[10px] opacity-60">{e.stage}</span>
          </div>
          {e.detail && <p className="mt-1 opacity-80">{e.detail}</p>}
          {e.target && <p className="mt-0.5 truncate text-[10px] opacity-60">{e.target}</p>}
        </li>
      ))}
    </ul>
  )
}

// --- Cost panel (cahier §15.6 — "le détail des coûts") --------------------

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-slate-800 bg-slate-900/40 px-2 py-1.5">
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className="font-mono text-sm text-slate-200">{value}</div>
    </div>
  )
}

export function CostPanel({ metrics }: { metrics: SessionMetrics | null }) {
  if (!metrics) {
    return <p className="text-xs text-slate-500">Per-session counters show here after a turn.</p>
  }
  const mean = metrics.turns ? metrics.latency_ms_total / metrics.turns : 0
  const secs = (ms: number) => `${(ms / 1000).toFixed(1)}s`
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2">
        <Stat label="turns" value={String(metrics.turns)} />
        <Stat label="llm calls" value={String(metrics.llm_calls)} />
        <Stat label="tokens" value={metrics.tokens.toLocaleString()} />
        <Stat label="guardrail events" value={String(metrics.guardrail_events)} />
        <Stat label="latency (last)" value={secs(metrics.latency_ms_last)} />
        <Stat label="latency (mean)" value={secs(mean)} />
      </div>
      {metrics.errors > 0 && (
        <div className="rounded-md border border-red-800/60 bg-red-950/30 p-2 text-xs text-red-300">
          {metrics.errors} error{metrics.errors === 1 ? '' : 's'} this session
        </div>
      )}
      {/* Tokens and calls, not currency: nothing in the backend prices a provider, and a
          made-up dollar figure on the results slide would be worse than none. */}
      <p className="text-[10px] leading-relaxed text-slate-600">
        Spend is reported in tokens and calls — FORGE does not price providers.
      </p>
    </div>
  )
}

// --- Tabbed side panel -----------------------------------------------------

type TabKey = 'results' | 'citations' | 'guardrails' | 'cost'

export function SidePanel(props: {
  tests: string | null
  verdict: string | null
  diff: string | null
  halted: string | null
  citations: CitationView[]
  grounded: boolean | null
  events: GuardrailEventView[]
  metrics: SessionMetrics | null
}) {
  const [tab, setTab] = useState<TabKey>('results')
  const firing = props.events.filter(isFiring).length
  const seen = useRef(firing)

  // A guardrail that fires while you are looking at the diff is not "visibly triggered"
  // (§15.6 wants the trigger *seen*), so a new non-allowed event pulls the panel to it.
  // Only non-allowed ones — routine `allowed` traffic must never steal the view.
  useEffect(() => {
    if (firing > seen.current) setTab('guardrails')
    seen.current = firing
  }, [firing])

  const tabs: { key: TabKey; label: string; badge?: number; alert?: boolean }[] = [
    { key: 'results', label: 'Results' },
    { key: 'citations', label: 'Citations', badge: props.citations.length },
    { key: 'guardrails', label: 'Guardrails', badge: props.events.length, alert: firing > 0 },
    { key: 'cost', label: 'Cost' },
  ]

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 border-b border-slate-800">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`flex flex-1 items-center justify-center gap-1 px-2 py-2.5 text-xs font-medium ${
              tab === t.key
                ? 'border-b-2 border-cyan-500 text-cyan-300'
                : 'text-slate-500 hover:text-slate-300'
            }`}
          >
            {t.label}
            {t.badge ? (
              <span
                className={`rounded-full px-1.5 text-[10px] ${
                  t.alert ? 'bg-red-900/70 text-red-200' : 'bg-slate-800 text-slate-400'
                }`}
              >
                {t.badge}
              </span>
            ) : null}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {tab === 'results' && (
          <ResultsPanel
            tests={props.tests}
            verdict={props.verdict}
            diff={props.diff}
            halted={props.halted}
          />
        )}
        {tab === 'citations' && (
          <CitationsPanel citations={props.citations} grounded={props.grounded} />
        )}
        {tab === 'guardrails' && <GuardrailsPanel events={props.events} />}
        {tab === 'cost' && <CostPanel metrics={props.metrics} />}
      </div>
    </div>
  )
}

// --- Results panel (tests, verdict, produced diff) -------------------------

export function ResultsPanel(props: {
  tests: string | null
  verdict: string | null
  diff: string | null
  halted: string | null
}) {
  const { tests, verdict, diff, halted } = props
  const green = tests ? /passed/.test(tests) && !/failed|error/i.test(tests) : false
  const nothing = !tests && !verdict && !diff && !halted
  // No header or scroller of its own — SidePanel owns both, and nesting them scrolled twice.
  return (
    <div className="space-y-4">
      {nothing && <p className="text-xs text-slate-500">Tests, review, and the diff show here.</p>}
      {halted && (
        <div className="rounded-md border border-yellow-800/60 bg-yellow-950/30 p-2 text-xs text-yellow-300">
          Stopped: {halted}
        </div>
      )}
      {tests && (
        <div>
          <div className="mb-1 text-xs text-slate-500">Sandbox tests</div>
          <div
            className={`rounded-md border p-2 text-xs ${
              green
                ? 'border-emerald-800/60 bg-emerald-950/30 text-emerald-300'
                : 'border-red-800/60 bg-red-950/30 text-red-300'
            }`}
          >
            {green ? '✓ ' : '✗ '}
            {tests}
          </div>
        </div>
      )}
      {verdict && (
        <div>
          <div className="mb-1 text-xs text-slate-500">Reviewer</div>
          <div className="rounded-md border border-fuchsia-800/50 bg-fuchsia-950/20 p-2 text-xs text-fuchsia-200">
            verdict: {verdict}
          </div>
        </div>
      )}
      {diff && (
        <div>
          <div className="mb-1 text-xs text-slate-500">Verified diff</div>
          <DiffViewer diff={diff} />
        </div>
      )}
    </div>
  )
}
