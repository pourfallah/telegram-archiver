import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, del, post } from '../lib/api'
import type { Account, ChatResult, ExportJob, ExportPreview } from '../lib/types'

function Progress({ status, processed, total }: { status: string; processed: number; total: number | null }) {
  const pct = total ? Math.round((processed / total) * 100) : 0
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 flex-1 overflow-hidden rounded bg-slate-800">
        <div className="h-full bg-emerald-500" style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-slate-400">
        {processed}{total ? ` / ${total} (${pct}%)` : ''} · {status}
      </span>
    </div>
  )
}

export default function Exports(): JSX.Element {
  const { data: accounts } = useQuery({ queryKey: ['accounts'], queryFn: () => api<Account[]>('/api/accounts') })
  const { data: exports_, refetch } = useQuery({
    queryKey: ['exports'],
    queryFn: () => api<ExportJob[]>('/api/exports'),
    refetchInterval: 3000,
  })

  const [accountId, setAccountId] = useState<number | ''>('')
  const [q, setQ] = useState('')
  const [results, setResults] = useState<ChatResult[]>([])
  const [format, setFormat] = useState('all')
  const [error, setError] = useState<string | null>(null)
  const [preview, setPreview] = useState<ExportPreview | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<ExportJob | null>(null)

  const search = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    try {
      setResults(await api<ChatResult[]>(`/api/accounts/${accountId}/chats?q=${encodeURIComponent(q)}`))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'search failed')
    }
  }

  const createExport = async (chat: ChatResult) => {
    setError(null)
    try {
      await post(`/api/accounts/${accountId}/exports`, { chat_id: chat.id, format, include_media: true })
      setResults([])
      refetch()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'export failed')
    }
  }

  const openPreview = async (id: number) => {
    try {
      setPreview(await api<ExportPreview>(`/api/exports/${id}/preview`))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'preview failed')
    }
  }

  const doDelete = async (id: number) => {
    try {
      await del(`/api/exports/${id}`)
      setConfirmDelete(null)
      refetch()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'delete failed')
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Exports</h1>

      <div className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-3">
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="block text-xs text-slate-400">Account</label>
            <select value={accountId} onChange={(e) => setAccountId(Number(e.target.value))}
              className="rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm">
              <option value="">Select account…</option>
              {accounts?.map((a) => <option key={a.id} value={a.id}>{a.phone}</option>)}
            </select>
          </div>
          <form onSubmit={search} className="flex items-end gap-2">
            <div>
              <label className="block text-xs text-slate-400">Search chat (username / title / id)</label>
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="family"
                className="rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm" />
            </div>
            <button className="rounded-md bg-slate-700 px-3 py-2 text-sm hover:bg-slate-600">Search</button>
          </form>
          <div>
            <label className="block text-xs text-slate-400">Format</label>
            <select value={format} onChange={(e) => setFormat(e.target.value)}
              className="rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm">
              <option value="all">All (JSON + HTML + SQLite)</option>
              <option value="json">JSON</option>
              <option value="html">HTML</option>
              <option value="sqlite">SQLite</option>
            </select>
          </div>
        </div>
        {error && <p className="text-sm text-rose-400">{error}</p>}
        {results.length > 0 && (
          <ul className="space-y-2">
            {results.map((c) => (
              <li key={c.id} className="flex items-center justify-between rounded-md border border-slate-700 bg-slate-800 px-3 py-2">
                <span className="text-sm">{c.title} <span className="text-xs text-slate-500">({c.type}{c.username ? ` · @${c.username}` : ''})</span></span>
                <button onClick={() => createExport(c)} className="rounded-md bg-emerald-600 px-2 py-1 text-xs hover:bg-emerald-500">
                  Export
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-3">
        {exports_?.map((e) => (
          <div key={e.id} className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-2">
            <div className="flex items-center justify-between">
              <span className="font-medium">{e.chat_title}</span>
              <span className="text-xs text-slate-400">{e.format} · {e.status}</span>
            </div>
            <Progress status={e.status} processed={e.messages_processed} total={e.total_messages_est && e.total_messages_est < 2 ** 31 - 1 ? e.total_messages_est : null} />
            <div className="flex gap-2 text-xs">
              <span className="text-slate-400">Media: {e.files_downloaded}/{e.files_total}</span>
              <span className="text-slate-400">Speed: {e.speed_mps.toFixed(2)} msg/s</span>
              {e.eta_seconds != null && <span className="text-slate-400">ETA: {Math.round(e.eta_seconds / 60)}m</span>}
            </div>
            {e.error && <p className="text-xs text-rose-400">{e.error}</p>}
            <div className="flex flex-wrap gap-2">
              {(e.status === 'running' || e.status === 'queued') && (
                <button onClick={() => post(`/api/exports/${e.id}/pause`).then(() => refetch())}
                  className="rounded-md border border-amber-600 px-2 py-1 text-xs text-amber-300 hover:bg-amber-600/20">
                  Pause
                </button>
              )}
              {(e.status === 'paused' || e.status === 'cancelled' || e.status === 'failed') && (
                <button onClick={() => post(`/api/exports/${e.id}/resume`).then(() => refetch())}
                  className="rounded-md border border-emerald-600 px-2 py-1 text-xs text-emerald-300 hover:bg-emerald-600/20">
                  Resume
                </button>
              )}
              {(e.status === 'running' || e.status === 'queued' || e.status === 'paused') && (
                <button onClick={() => post(`/api/exports/${e.id}/cancel`).then(() => refetch())}
                  className="rounded-md border border-rose-600 px-2 py-1 text-xs text-rose-300 hover:bg-rose-600/20">
                  Cancel
                </button>
              )}
              {e.messages_processed > 0 && (
                <button onClick={() => openPreview(e.id)}
                  className="rounded-md border border-sky-600 px-2 py-1 text-xs text-sky-300 hover:bg-sky-600/20">
                  Preview
                </button>
              )}
              {(e.status === 'cancelled' || e.status === 'completed' || e.status === 'failed') && (
                <button onClick={() => setConfirmDelete(e)}
                  className="rounded-md border border-slate-700 px-2 py-1 text-xs text-rose-300 hover:bg-rose-600/20">
                  Delete
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

      {preview && (
        <div className="fixed inset-0 flex items-center justify-center bg-black/60 p-4" onClick={() => setPreview(null)}>
          <div className="flex max-h-[80vh] w-full max-w-2xl flex-col rounded-xl border border-slate-800 bg-slate-900" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-slate-800 p-4">
              <span className="font-medium">
                Export preview · first {preview.messages.length} of {preview.total_messages}
                {preview.partial ? ' (partial)' : ''}
              </span>
              <span className="flex items-center gap-2">
                {preview.verification_status === 'PASS' ? (
                  <span className="rounded bg-emerald-900/60 px-2 py-0.5 text-xs text-emerald-300">verified: PASS</span>
                ) : (
                  <span className="rounded bg-amber-900/60 px-2 py-0.5 text-xs text-amber-300">not verified</span>
                )}
                <button onClick={() => setPreview(null)} className="text-slate-400 hover:text-white">✕</button>
              </span>
            </div>
            <div className="space-y-3 overflow-y-auto p-4 text-sm">
              {preview.messages.length === 0 && <p className="text-slate-400">No messages yet.</p>}
              {preview.messages.map((m) => (
                <div key={m.id} className="rounded-md border border-slate-800 bg-slate-950 p-3">
                  <div className="flex justify-between text-xs text-slate-500">
                    <span className="font-medium text-slate-300">
                      {m.sender ?? 'Unknown'} <span className="text-slate-600">#{m.id}</span>
                    </span>
                    <span>{m.date ?? ''}</span>
                  </div>
                  {m.media_label && (
                    <div className="mt-1 text-xs font-medium text-sky-400">
                      📎 media: {m.media?.join(', ')}
                      {m.grouped_id != null && <span className="ml-1 text-slate-500">(group {m.grouped_id})</span>}
                    </div>
                  )}
                  {m.caption ? (
                    <p className="mt-1 whitespace-pre-wrap">
                      {m.caption}
                      <span className="ml-1 text-xs text-slate-500">[caption]</span>
                    </p>
                  ) : m.text ? (
                    <p className="mt-1 whitespace-pre-wrap">{m.text}</p>
                  ) : null}
                  {m.reply_to != null && (
                    <p className="mt-1 text-xs text-purple-400">↩ reply to message #{m.reply_to}</p>
                  )}
                  {m.forwarded_from && (
                    <p className="mt-1 text-xs text-orange-400">
                      ⏩ forwarded
                      {m.forwarded_from.from_name ? ` from ${m.forwarded_from.from_name}` : ''}
                      {m.forwarded_from.from_id ? ` (id ${m.forwarded_from.from_id})` : ''}
                    </p>
                  )}
                  {m.reactions?.reactions && m.reactions.reactions.length > 0 && (
                    <p className="mt-1 text-xs text-rose-400">
                      {m.reactions.reactions.map((r) => `${r.emoji}×${r.count ?? 1}`).join(' ')}
                    </p>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {confirmDelete && (
        <div className="fixed inset-0 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-sm space-y-3 rounded-xl border border-slate-800 bg-slate-900 p-5">
            <h2 className="font-medium">Delete this export?</h2>
            <p className="text-sm text-slate-400">
              <span className="text-slate-200">{confirmDelete.chat_title}</span> — this permanently removes the
              export record and <strong>all its files and media</strong> from disk. This cannot be undone.
            </p>
            <div className="flex gap-2">
              <button onClick={() => doDelete(confirmDelete.id)} className="rounded-md bg-rose-600 px-3 py-2 text-sm font-medium hover:bg-rose-500">
                Delete permanently
              </button>
              <button onClick={() => setConfirmDelete(null)} className="rounded-md border border-slate-700 px-3 py-2 text-sm hover:bg-slate-800">
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}