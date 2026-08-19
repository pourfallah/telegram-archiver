import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, post } from '../lib/api'
import type { Account, ExportJob, PeerValidationResult, ImportJobPublic } from '../lib/types'

const TEST_COUNTS = [10, 50, 100, 250, 500, 900, 999, 1000, 1100, 2000]

export default function TestImport(): JSX.Element {
  const navigate = useNavigate()
  const { data: accounts } = useQuery({ queryKey: ['accounts'], queryFn: () => api<Account[]>('/api/accounts') })
  const { data: exports } = useQuery({ queryKey: ['exports'], queryFn: () => api<ExportJob[]>('/api/exports') })

  const [sourceAccountId, setSourceAccountId] = useState<number | ''>('')
  const [exportId, setExportId] = useState<number | ''>('')
  const [targetAccountId, setTargetAccountId] = useState<number | ''>('')
  const [contactIdentifier, setContactIdentifier] = useState<string>('')
  const [count, setCount] = useState<number>(10)
  const [peerValidation, setPeerValidation] = useState<PeerValidationResult | null>(null)
  const [_error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState<boolean>(false)

  const sourceExports = exports?.filter((e) => e.account_id === sourceAccountId && e.messages_processed > 0) || []

  const validatePeer = async () => {
    if (!sourceAccountId || !contactIdentifier) {
      setError('Select source account and enter contact identifier')
      return
    }
    setError(null)
    setPeerValidation(null)
    setLoading(true)
    try {
      const res = await post<PeerValidationResult>(`/api/import/${sourceAccountId}/validate-peer`, {
        export_id: exportId,
        contact_identifier: contactIdentifier,
        count,
        target_peer_id: undefined,
      })
      setPeerValidation(res)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'peer validation failed')
    } finally {
      setLoading(false)
    }
  }

  const startTest = async () => {
    if (!peerValidation?.allowed) {
      setError('Peer validation must pass first')
      return
    }
    setError(null)
    setLoading(true)
    try {
      const res = await post<ImportJobPublic>(`/api/import/${targetAccountId}/test-import`, {
        export_id: Number(exportId),
        target_peer_id: peerValidation.peer.peer_id || undefined,
        contact_identifier: contactIdentifier,
        count,
      })
      navigate(`/import/jobs/${res.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'test import failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-semibold">Test Import (Real Telegram MTProto)</h1>
      <p className="text-sm text-slate-400">
        Run a small real import into an existing peer using Telegram's history-import API.
        Choose message count, validate the peer, then start the test job.
      </p>

      <section className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-4">
        <h2 className="font-medium">Step 1: Select Source Export</h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs text-slate-400">Source Account (Account A)</label>
            <select value={sourceAccountId} onChange={(e) => { setSourceAccountId(Number(e.target.value)); setExportId('') }}
              className="rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm">
              <option value="">Select…</option>
              {accounts?.filter((a) => a.status === 'active').map((a) => <option key={a.id} value={a.id}>{a.phone}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs text-slate-400">Export from Account A</label>
            <select value={exportId} onChange={(e) => setExportId(Number(e.target.value))}
              className="rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm">
              <option value="">Select…</option>
              {sourceExports.map((x) => <option key={x.id} value={x.id}>{x.chat_title} (#{x.id} · {x.messages_processed} msgs)</option>)}
            </select>
          </div>
        </div>
      </section>

      <section className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-4">
        <h2 className="font-medium">Step 2: Target Peer (Account B's existing A↔B chat)</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div>
            <label className="block text-xs text-slate-400">Target Account (Account B)</label>
            <select value={targetAccountId} onChange={(e) => setTargetAccountId(Number(e.target.value))}
              className="rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm">
              <option value="">Select…</option>
              {accounts?.filter((a) => a.status === 'active').map((a) => <option key={a.id} value={a.id}>{a.phone}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs text-slate-400">Contact identifier (username/phone/id of A)</label>
            <input value={contactIdentifier} onChange={(e) => setContactIdentifier(e.target.value)}
              className="rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm"
              placeholder="@username or +1234567890 or 123456789" />
          </div>
          <div>
            <label className="block text-xs text-slate-400">Messages to import</label>
            <select value={count} onChange={(e) => setCount(Number(e.target.value))}
              className="rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm">
              {TEST_COUNTS.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
        </div>
        <button onClick={validatePeer} disabled={loading} className="rounded-md bg-slate-700 px-3 py-2 text-sm hover:bg-slate-600 disabled:opacity-50">
          {loading ? 'Validating…' : 'Validate Peer'}
        </button>
      </section>

      {peerValidation && (
        <section className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-3">
          <h2 className="font-medium">Peer Validation Result</h2>
          <p className="text-sm">
            <span className={peerValidation.allowed ? 'text-emerald-400' : 'text-rose-400'}>
              {peerValidation.allowed ? 'ALLOWED' : 'BLOCKED'}
            </span>
          </p>
          {peerValidation.confirm_text && <p className="text-sm text-slate-300">{peerValidation.confirm_text}</p>}
          {peerValidation.error_code && <p className="text-sm text-rose-400">{peerValidation.error_code}: {peerValidation.error_message}</p>}
          <div className="text-sm text-slate-400">
            Peer: {peerValidation.peer.title} (@{peerValidation.peer.username}) · Type: {peerValidation.peer.peer_type}
            · Messages: {peerValidation.peer.message_count ?? 'unknown'} · Mutual contact: {peerValidation.peer.mutual_contact === true ? 'YES' : peerValidation.peer.mutual_contact === false ? 'NO' : 'UNKNOWN'}
          </div>
        </section>
      )}

      {peerValidation?.allowed && (
        <section className="rounded-xl border border-emerald-800 bg-emerald-950/30 p-4 space-y-3">
          <h2 className="font-medium text-emerald-400">Step 3: Start Test Import</h2>
          <p className="text-sm text-slate-300">This will dispatch a real Telegram import job via the MTProto API.</p>
          <button onClick={startTest} disabled={loading} className="rounded-md bg-emerald-600 px-4 py-2 text-sm hover:bg-emerald-500 disabled:opacity-50">
            {loading ? 'Starting…' : `Start ${count}-Message Test Import`}
          </button>
        </section>
      )}
    </div>
  )
}