import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, post } from '../lib/api'
import type { ExportJob, ImportPackage, MigrationJob } from '../lib/types'

const TEST_COUNTS = [10, 50, 100, 500, 1000]

export default function Migration(): JSX.Element {
  const { data: exports_ } = useQuery({
    queryKey: ['exports'],
    queryFn: () => api<ExportJob[]>('/api/exports'),
    refetchInterval: 5000,
  })
  const { data: packages, refetch } = useQuery({
    queryKey: ['packages'],
    queryFn: () => api<ImportPackage[]>('/api/import/packages'),
  })
  const { data: migrations, refetch: refetchMigrations } = useQuery({
    queryKey: ['migrations'],
    queryFn: () => api<MigrationJob[]>('/api/migrations'),
    refetchInterval: 5000,
  })

  const [exportId, setExportId] = useState<number | ''>('')
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const convert = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setMessage(null)
    try {
      await post('/api/migrations', { export_id: Number(exportId) })
      setMessage('Migration started.')
      refetchMigrations()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'migration failed')
    }
  }

  const makeTest = async (count: number) => {
    setError(null)
    setMessage(null)
    try {
      const pkg = await post<ImportPackage>('/api/migrations/test', { count })
      setMessage(`Test package created: ${pkg.name}`)
      refetch()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'test package failed')
    }
  }

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-semibold">Migration Builder</h1>

      <section className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-3">
        <h2 className="font-medium">Convert a completed export to a WhatsApp package</h2>
        <p className="text-sm text-slate-400">
          Produces <code className="text-slate-200">_chat.txt</code> + media for Telegram&apos;s
          official importer (Settings → Advanced → Import from WhatsApp).
        </p>
        <form onSubmit={convert} className="flex items-end gap-2">
          <div>
            <label className="block text-xs text-slate-400">Export (any with data — partial OK)</label>
            <select value={exportId} onChange={(e) => setExportId(Number(e.target.value))}
              className="rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm">
              <option value="">Select…</option>
              {exports_?.filter((x) => x.messages_processed > 0).map((x) => (
                <option key={x.id} value={x.id}>{x.chat_title} (#{x.id} · {x.status} · {x.messages_processed} msgs)</option>
              ))}
            </select>
          </div>
          <button className="rounded-md bg-emerald-600 px-3 py-2 text-sm hover:bg-emerald-500">Convert</button>
        </form>
        {message && <p className="text-sm text-emerald-400">{message}</p>}
        {error && <p className="text-sm text-rose-400">{error}</p>}
      </section>

      <section className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-3">
        <h2 className="font-medium">Test migration builder</h2>
        <div className="flex gap-2">
          {TEST_COUNTS.map((c) => (
            <button key={c} onClick={() => makeTest(c)} className="rounded-md border border-slate-700 px-3 py-2 text-sm hover:bg-slate-800">
              {c}
            </button>
          ))}
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="font-medium">Recent migrations</h2>
        {migrations?.map((m) => (
          <div key={m.id} className="flex items-center justify-between rounded-xl border border-slate-800 bg-slate-900 p-4 text-sm">
            <span>Export #{m.chat_export_id} · {m.status}</span>
            <span className="text-slate-400">{m.messages_converted} messages · {m.media_copied} media</span>
          </div>
        ))}
        <h2 className="font-medium pt-2">Packages</h2>
        {packages?.map((p) => (
          <div key={p.id} className="rounded-xl border border-slate-800 bg-slate-900 p-4 text-sm">
            <div className="flex items-center justify-between">
              <span className="font-medium">{p.name}</span>
              <span className="text-xs text-slate-400">validation: {p.validation_status}</span>
            </div>
            <p className="mt-1 text-slate-400">
              {p.messages_count} messages · {p.media_count} media · {p.users_detected ? Object.keys(p.users_detected).length : 0} users
            </p>
          </div>
        ))}
      </section>
    </div>
  )
}