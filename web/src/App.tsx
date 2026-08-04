import { useCallback, useEffect, useRef, useState } from 'react'
import * as api from './api'
import type {
  CitationView,
  GuardrailEventView,
  InterruptFrame,
  NodeFrame,
  SessionInfo,
  SessionMetrics,
  StreamHandlers,
} from './api'
import { ApprovalModal, Sidebar, SidePanel, Timeline } from './components'
import type { TimelineItem } from './components'

interface Msg {
  role: 'user' | 'assistant'
  content: string
}

// The salient field of a node frame, for the timeline line.
function nodeDetail(f: NodeFrame): string {
  if (f.halted) return `halted: ${f.halted}`
  if (f.tests) return f.tests
  if (f.verdict) return `verdict ${f.verdict}`
  if (typeof f.plan_steps === 'number') return `${f.plan_steps} plan step(s)`
  if (typeof f.chunks === 'number') return `${f.chunks} chunk(s) retrieved`
  if (typeof f.patch_ok === 'boolean') return f.patch_ok ? 'patch applies' : 'patch failed to apply'
  if (typeof f.iteration === 'number') return `iteration ${f.iteration}`
  if (f.citations) return `${f.citations.length} citation(s), ${f.grounded ? 'grounded' : 'ungrounded'}`
  return ''
}

export default function App() {
  const [sessions, setSessions] = useState<SessionInfo[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [messages, setMessages] = useState<Msg[]>([])
  const [streaming, setStreaming] = useState('')
  const [timeline, setTimeline] = useState<TimelineItem[]>([])
  const [interrupt, setInterrupt] = useState<InterruptFrame | null>(null)
  const [tests, setTests] = useState<string | null>(null)
  const [verdict, setVerdict] = useState<string | null>(null)
  const [diff, setDiff] = useState<string | null>(null)
  const [halted, setHalted] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [input, setInput] = useState('')
  const [citations, setCitations] = useState<CitationView[]>([])
  const [grounded, setGrounded] = useState<boolean | null>(null)
  const [events, setEvents] = useState<GuardrailEventView[]>([])
  const [metrics, setMetrics] = useState<SessionMetrics | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // Which path the next message takes. The session stream runs the change graph and the
  // supervisor has no CHANGE route, so the two cannot be inferred from one entry point —
  // see STATE.md "ask-vs-change routing" for the deviation this records.
  const [mode, setMode] = useState<'ask' | 'change'>('ask')
  const [repos, setRepos] = useState<api.RepoOption[]>([])

  const tlId = useRef(0)
  const streamRef = useRef('')
  const scrollRef = useRef<HTMLDivElement>(null)

  const refreshSessions = useCallback(async () => {
    try {
      setSessions(await api.listSessions())
    } catch (e) {
      setError(String(e))
    }
  }, [])

  const refreshRepos = useCallback(async () => {
    try {
      setRepos(await api.listRepos())
    } catch {
      // A deployment with no selectable roots is a valid configuration, not an error
      // to shout about — the picker simply does not render.
      setRepos([])
    }
  }, [])

  useEffect(() => {
    void refreshSessions()
    void refreshRepos()
  }, [refreshSessions, refreshRepos])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [messages, streaming, timeline])

  // Guardrail events and metrics are session-scoped and cumulative, so they are refetched
  // rather than reset: the log is the record of the whole session, not of one turn.
  const refreshSideData = useCallback(async (id: string) => {
    try {
      const [ev, m] = await Promise.all([api.getGuardrailEvents(id), api.getMetrics(id)])
      setEvents(ev)
      setMetrics(m.per_session[id] ?? m.totals)
    } catch {
      /* diagnostics panels — a failed refresh must not take down the run view */
    }
  }, [])

  const resetRun = () => {
    setTimeline([])
    setTests(null)
    setVerdict(null)
    setDiff(null)
    setHalted(null)
    setError(null)
    setStreaming('')
    setCitations([])
    setGrounded(null)
    streamRef.current = ''
  }

  const commitStreaming = () => {
    if (streamRef.current.trim()) {
      const text = streamRef.current
      setMessages((m) => [...m, { role: 'assistant', content: text }])
    }
    streamRef.current = ''
    setStreaming('')
  }

  // `id` is threaded in rather than read off `activeId`: a stream outlives the render that
  // started it, and the side-panel refresh must hit the session the run belongs to.
  const handlers = (id: string): StreamHandlers => ({
    onNode: (f) => {
      setTimeline((t) => [...t, { id: tlId.current++, node: f.node, detail: nodeDetail(f) }])
      if (f.tests) setTests(f.tests)
      if (f.verdict) setVerdict(f.verdict)
      if (f.halted) setHalted(f.halted)
      // An empty array is still a present answer — check the key, not its length.
      if (f.citations) {
        setCitations(f.citations)
        setGrounded(f.grounded ?? false)
      }
    },
    onToken: (text) => {
      streamRef.current += text
      setStreaming(streamRef.current)
    },
    onInterrupt: (f) => {
      if (f.payload.kind === 'patch_approval') setDiff(f.payload.diff ?? null)
      setInterrupt(f)
      setRunning(false)
      void refreshSideData(id)
    },
    onDone: () => {
      commitStreaming()
      setRunning(false)
      void refreshSideData(id)
    },
    onError: (msg) => {
      setError(msg)
      setRunning(false)
      // A blocked input ends as an error frame — the reason is in the guardrail log.
      void refreshSideData(id)
    },
  })

  const send = async () => {
    const text = input.trim()
    if (!text || !activeId || running) return
    setInput('')
    setMessages((m) => [...m, { role: 'user', content: text }])
    resetRun()
    setRunning(true)
    if (mode === 'ask') {
      // Grounded Q&A is a plain request/response — no gates to pause at and no graph
      // to narrate, so there is nothing for SSE to stream.
      try {
        const a = await api.ask(text, activeId)
        setMessages((m) => [...m, { role: 'assistant', content: a.answer }])
        setCitations(a.citations ?? [])
        setGrounded(a.grounded)
      } catch (e) {
        setError(String(e))
      }
      setRunning(false)
      void refreshSideData(activeId)
      return
    }
    await api.sendMessage(activeId, text, handlers(activeId))
  }

  const decide = async (approved: boolean) => {
    if (!activeId || !interrupt) return
    setInterrupt(null)
    setRunning(true)
    await api.approve(activeId, approved, handlers(activeId))
  }

  const reindex = async () => {
    try {
      const r = await api.startIndex()
      setNotice(`rebuilding the index for ${r.path} — this runs in the background…`)
    } catch (e) {
      setError(String(e))
    }
  }

  const upload = async (files: FileList) => {
    try {
      const r = await api.uploadFiles(files)
      await refreshRepos()   // the drop folder may have just come into existence
      const bad = Object.entries(r.refused)
      const refused = bad.length ? ` Refused: ${bad.map(([n, why]) => `${n} (${why})`).join(', ')}.` : ''
      setNotice(
        `${r.stored.length} file(s) → ${r.upload_dir}, ${r.total_files} there now.` +
          ` Select that folder above and rebuild to index them.${refused}`,
      )
    } catch (e) {
      setError(String(e))
    }
  }

  const pickRepo = async (path: string) => {
    try {
      const r = await api.setTarget(path)
      await refreshRepos()
      // Switching does not reindex, and saying nothing here is how someone ends up
      // asking questions about the previous repository and believing the answers.
      const stale = r.indexed
        ? 'the existing index is for whatever was indexed last — rebuild it'
        : 'nothing is indexed for it yet — rebuild the index'
      const sessions =
        r.active_sessions > 0
          ? ` ${r.active_sessions} existing session(s) still hold worktrees from the previous repo.`
          : ''
      setNotice(`target repo → ${r.target_repo}; ${stale}.${sessions}`)
    } catch (e) {
      setError(String(e))
    }
  }

  const newSession = async () => {
    try {
      const s = await api.createSession()
      await refreshSessions()
      void selectSession(s.session_id)
    } catch (e) {
      setError(String(e))
    }
  }

  const selectSession = async (id: string) => {
    setActiveId(id)
    setMessages([])
    resetRun()
    setEvents([])
    setMetrics(null)
    void refreshSideData(id)
    try {
      const h = await api.getHistory(id)
      setMessages(
        h.messages.map((t) => ({
          role: t.role.toLowerCase().includes('human') ? 'user' : 'assistant',
          content: t.content,
        })),
      )
      if (h.halted) setHalted(h.halted)
    } catch (e) {
      setError(String(e))
    }
  }

  const removeSession = async (id: string) => {
    try {
      await api.deleteSession(id)
    } finally {
      if (activeId === id) {
        setActiveId(null)
        setMessages([])
        resetRun()
        setEvents([])
        setMetrics(null)
      }
      void refreshSessions()
    }
  }

  return (
    <div className="flex h-screen w-screen overflow-hidden text-slate-200">
      <Sidebar
        sessions={sessions}
        activeId={activeId}
        busy={running}
        onSelect={selectSession}
        onNew={newSession}
        onDelete={removeSession}
        onIndex={() => void reindex()}
        repos={repos}
        onPickRepo={(p) => void pickRepo(p)}
        onUpload={(f) => void upload(f)}
      />

      <main className="flex min-w-0 flex-1">
        {/* Chat column */}
        <section className="flex min-w-0 flex-1 flex-col bg-[#0b0f14]">
          <header className="flex items-center gap-3 border-b border-slate-800 px-5 py-3">
            <span className="text-sm text-slate-400">
              {activeId ? (
                <>
                  session <code className="text-cyan-300">{activeId}</code>
                </>
              ) : (
                'No session selected'
              )}
            </span>
            {running && <span className="text-xs text-cyan-400">● streaming</span>}
            {notice && <span className="text-xs text-slate-500">{notice}</span>}
            {error && <span className="ml-auto text-xs text-red-400">{error}</span>}
          </header>

          <div ref={scrollRef} className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-5">
            {!activeId && (
              <div className="mt-20 text-center text-sm text-slate-500">
                Create or select a session to begin. Ask a question, or request a change and
                approve the plan and patch as they arrive.
              </div>
            )}
            {messages.map((m, i) => (
              <Bubble key={i} role={m.role} content={m.content} />
            ))}
            {streaming && <Bubble role="assistant" content={streaming} />}
          </div>

          <div className="border-t border-slate-800 p-3">
            <div className="mb-2 flex items-center gap-1">
              {(['ask', 'change'] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setMode(m)}
                  disabled={running}
                  className={`rounded-md px-2.5 py-1 text-xs font-medium disabled:opacity-50 ${
                    mode === m
                      ? 'bg-cyan-600/20 text-cyan-300 ring-1 ring-cyan-700'
                      : 'text-slate-500 hover:text-slate-300'
                  }`}
                >
                  {m === 'ask' ? 'Ask' : 'Change'}
                </button>
              ))}
              <span className="ml-2 text-[10px] text-slate-600">
                {mode === 'ask'
                  ? 'grounded answer with citations'
                  : 'plan → tests → patch, with both approval gates'}
              </span>
            </div>
            <div className="flex items-end gap-2">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    void send()
                  }
                }}
                placeholder={
                  activeId
                    ? mode === 'ask'
                      ? 'Ask about the codebase…'
                      : 'Describe the change or paste a bug report…'
                    : 'Select a session first'
                }
                disabled={!activeId || running}
                rows={2}
                className="min-h-0 flex-1 resize-none rounded-md border border-slate-700 bg-[#0d1117] px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:border-cyan-700 focus:outline-none disabled:opacity-50"
              />
              <button
                onClick={() => void send()}
                disabled={!activeId || running || !input.trim()}
                className="rounded-md bg-cyan-600 px-4 py-2 text-sm font-medium text-white hover:bg-cyan-500 disabled:opacity-40"
              >
                Send
              </button>
            </div>
          </div>
        </section>

        {/* Right column: timeline above, tabbed detail below */}
        <section className="flex w-[26rem] shrink-0 flex-col border-l border-slate-800 bg-[#0d1117]">
          <div className="min-h-0 flex-1 border-b border-slate-800">
            <Timeline items={timeline} running={running} />
          </div>
          <div className="min-h-0 flex-1">
            <SidePanel
              tests={tests}
              verdict={verdict}
              diff={diff}
              halted={halted}
              citations={citations}
              grounded={grounded}
              events={events}
              metrics={metrics}
            />
          </div>
        </section>
      </main>

      {interrupt && (
        <ApprovalModal
          payload={interrupt.payload}
          busy={running}
          onApprove={() => void decide(true)}
          onReject={() => void decide(false)}
        />
      )}
    </div>
  )
}

function Bubble({ role, content }: { role: 'user' | 'assistant'; content: string }) {
  const user = role === 'user'
  return (
    <div className={`flex ${user ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[80%] whitespace-pre-wrap rounded-2xl px-4 py-2 text-sm ${
          user
            ? 'bg-cyan-600 text-white'
            : 'border border-slate-800 bg-slate-900/60 text-slate-200'
        }`}
      >
        {content}
      </div>
    </div>
  )
}
