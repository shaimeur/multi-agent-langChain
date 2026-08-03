// Typed client for the FORGE FastAPI surface (cahier §11). The SSE endpoints are POST
// with a body, so native EventSource (GET-only) will not do — we POST and parse the
// text/event-stream off a fetch ReadableStream ourselves. Frames are already typed by
// the server (node/token/interrupt/done/error), so the UI renders frames, never
// LangGraph's tuples.

const BASE = import.meta.env.VITE_API_BASE ?? ''

// --- REST shapes -----------------------------------------------------------

export interface SessionInfo {
  session_id: string
  created_at: string
  workspace: string
  branch: string
}

export interface HistoryTurn {
  role: string
  content: string
}

export interface SessionHistory {
  session_id: string
  messages: HistoryTurn[]
  awaiting_approval: boolean
  halted: string
}

export interface GroundedAnswer {
  question: string
  answer: string
  grounded: boolean
  citations: CitationView[]
}

export interface GuardrailEventView {
  session_id: string
  stage: string
  rule: string
  action: string
  score: number
  detail: string
  target: string
  created_at: string
}

export interface SessionMetrics {
  turns: number
  llm_calls: number
  tokens: number
  guardrail_events: number
  latency_ms_total: number
  latency_ms_last: number
  errors: number
}

export interface MetricsResponse {
  sessions: number
  totals: SessionMetrics
  per_session: Record<string, SessionMetrics>
  guardrail_events: number
}

// --- SSE frame payloads ----------------------------------------------------

export interface CitationView {
  path: string
  start_line: number
  end_line: number
}

export interface NodeFrame {
  node: string
  chunks?: number
  plan_steps?: number
  patch_ok?: boolean
  tests?: string
  verdict?: string
  halted?: string
  iteration?: number
  grounded?: boolean
  citations?: CitationView[]
}

export interface PlanStepView {
  intent: string
  target_path: string
  rationale: string
}

export interface PlanPayload {
  kind: 'plan_approval'
  summary: string
  steps: PlanStepView[]
  blast_radius: string[]
}

export interface PatchPayload {
  kind: 'patch_approval'
  diff: string
  target_path?: string
}

export type ApprovalPayload = PlanPayload | PatchPayload

export interface InterruptFrame {
  awaiting: string
  payload: ApprovalPayload
}

export interface StreamHandlers {
  onNode?: (f: NodeFrame) => void
  onToken?: (text: string) => void
  onInterrupt?: (f: InterruptFrame) => void
  onDone?: (next: string[]) => void
  onError?: (message: string) => void
}

// --- REST ------------------------------------------------------------------

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json() as Promise<T>
}

export const listSessions = () => fetch(`${BASE}/v1/sessions`).then(json<SessionInfo[]>)

export const createSession = () =>
  fetch(`${BASE}/v1/sessions`, { method: 'POST' }).then(json<SessionInfo>)

export const deleteSession = (id: string) =>
  fetch(`${BASE}/v1/sessions/${id}`, { method: 'DELETE' })

export const getHistory = (id: string) =>
  fetch(`${BASE}/v1/sessions/${id}/history`).then(json<SessionHistory>)

// The grounded Q&A route (cahier §11). Deliberately separate from the session stream:
// that stream runs the *change* graph, whose supervisor has no route to an answer, so a
// question sent down it gets planned instead of answered. `session_id` is passed so the
// §8.5 log still attributes this turn's guardrail events to the session.
export const ask = (question: string, sessionId: string, k = 8) =>
  fetch(`${BASE}/v1/ask`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, session_id: sessionId, k }),
  }).then(json<GroundedAnswer>)

// The §8.5 guardrail log, newest first — scoped to one session so the panel shows
// "this run's events", not every event the process has ever logged.
export const getGuardrailEvents = (id: string, limit = 50) =>
  fetch(`${BASE}/v1/guardrails/events?session_id=${encodeURIComponent(id)}&limit=${limit}`).then(
    json<GuardrailEventView[]>,
  )

export const getMetrics = (id: string) =>
  fetch(`${BASE}/v1/metrics?session_id=${encodeURIComponent(id)}`).then(json<MetricsResponse>)

// 202 and indexes in the background — the demo pre-warms, so this is the "no terminal"
// escape hatch rather than something to wait on.
export const startIndex = (incremental = true) =>
  fetch(`${BASE}/v1/index`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ incremental }),
  }).then(json<{ status: string; path: string; incremental: boolean }>)

// --- SSE -------------------------------------------------------------------

function dispatch(type: string, data: Record<string, unknown>, h: StreamHandlers) {
  switch (type) {
    case 'node':
      h.onNode?.(data as unknown as NodeFrame)
      break
    case 'token':
      h.onToken?.(String(data.text ?? ''))
      break
    case 'interrupt':
      h.onInterrupt?.(data as unknown as InterruptFrame)
      break
    case 'done':
      h.onDone?.((data.next as string[]) ?? [])
      break
    case 'error':
      h.onError?.(String(data.message ?? 'stream error'))
      break
  }
}

async function stream(url: string, body: unknown, h: StreamHandlers): Promise<void> {
  let res: Response
  try {
    res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch (e) {
    h.onError?.(`network: ${(e as Error).message}`)
    return
  }
  if (!res.ok || !res.body) {
    h.onError?.(`HTTP ${res.status}`)
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let event = 'message'
  let dataLines: string[] = []

  const flush = () => {
    if (dataLines.length) {
      try {
        dispatch(event, JSON.parse(dataLines.join('\n')), h)
      } catch {
        /* a malformed frame is not worth aborting the stream over */
      }
    }
    event = 'message'
    dataLines = []
  }

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let nl: number
    // Frames are CRLF-terminated on this server; split on \n and drop a trailing \r.
    while ((nl = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, nl).replace(/\r$/, '')
      buffer = buffer.slice(nl + 1)
      if (line === '') {
        flush()
      } else if (line.startsWith('event:')) {
        event = line.slice(6).trim()
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).replace(/^ /, ''))
      }
    }
  }
  flush()
}

export const sendMessage = (id: string, message: string, h: StreamHandlers) =>
  stream(`${BASE}/v1/sessions/${id}/messages`, { message }, h)

export const approve = (id: string, approved: boolean, h: StreamHandlers, feedback = '') =>
  stream(`${BASE}/v1/sessions/${id}/approve`, { approved, feedback }, h)
