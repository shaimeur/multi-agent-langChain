import { useCallback, useEffect, useRef, useState } from 'react'
import * as api from './api'
import type { InterruptFrame, NodeFrame, SessionInfo, StreamHandlers } from './api'
import { ApprovalModal, ResultsPanel, Sidebar, Timeline } from './components'
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

  useEffect(() => {
    void refreshSessions()
  }, [refreshSessions])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [messages, streaming, timeline])

  const resetRun = () => {
    setTimeline([])
    setTests(null)
    setVerdict(null)
    setDiff(null)
    setHalted(null)
    setError(null)
    setStreaming('')
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

  const handlers = (): StreamHandlers => ({
    onNode: (f) => {
      setTimeline((t) => [...t, { id: tlId.current++, node: f.node, detail: nodeDetail(f) }])
      if (f.tests) setTests(f.tests)
      if (f.verdict) setVerdict(f.verdict)
      if (f.halted) setHalted(f.halted)
    },
    onToken: (text) => {
      streamRef.current += text
      setStreaming(streamRef.current)
    },
    onInterrupt: (f) => {
      if (f.payload.kind === 'patch_approval') setDiff(f.payload.diff ?? null)
      setInterrupt(f)
      setRunning(false)
    },
    onDone: () => {
      commitStreaming()
      setRunning(false)
    },
    onError: (msg) => {
      setError(msg)
      setRunning(false)
    },
  })

  const send = async () => {
    const text = input.trim()
    if (!text || !activeId || running) return
    setInput('')
    setMessages((m) => [...m, { role: 'user', content: text }])
    resetRun()
    setRunning(true)
    await api.sendMessage(activeId, text, handlers())
  }

  const decide = async (approved: boolean) => {
    if (!activeId || !interrupt) return
    setInterrupt(null)
    setRunning(true)
    await api.approve(activeId, approved, handlers())
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
                placeholder={activeId ? 'Ask, or describe a change…' : 'Select a session first'}
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

        {/* Right column: timeline + results */}
        <section className="flex w-96 shrink-0 flex-col border-l border-slate-800 bg-[#0d1117]">
          <div className="min-h-0 flex-1 border-b border-slate-800">
            <Timeline items={timeline} running={running} />
          </div>
          <div className="min-h-0 flex-1">
            <ResultsPanel tests={tests} verdict={verdict} diff={diff} halted={halted} />
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
