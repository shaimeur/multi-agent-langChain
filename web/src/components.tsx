// Presentational components for the FORGE UI. All state lives in App; these render.
import type { ApprovalPayload, PlanPayload, SessionInfo } from './api'

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
  onSelect: (id: string) => void
  onNew: () => void
  onDelete: (id: string) => void
}) {
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
  return (
    <div className="flex h-full flex-col">
      <h2 className="border-b border-slate-800 px-4 py-3 text-xs font-semibold uppercase tracking-wider text-slate-400">
        Results
      </h2>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-3">
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
    </div>
  )
}
