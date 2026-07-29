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

// --- SSE frame payloads ----------------------------------------------------

export interface NodeFrame {
  node: string
  chunks?: number
  plan_steps?: number
  patch_ok?: boolean
  tests?: string
  verdict?: string
  halted?: string
  iteration?: number
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
