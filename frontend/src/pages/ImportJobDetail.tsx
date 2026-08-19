import { useQuery } from '@tanstack/react-query'
import { useParams } from 'react-router-dom'
import { api, post } from '../lib/api'
import type { ImportJobPublic } from '../lib/types'

interface VerificationReport {
  overall: string
  counts: { source: number; target: number; matched: number }
  checks: { sender_order: boolean; timestamp: boolean; text: boolean; media: boolean }
}

export default function ImportJobDetail(): JSX.Element {
  const { id } = useParams<{ id: string }>()
  const jobId = Number(id)

  const { data: job, refetch } = useQuery({
    queryKey: ['importJob', jobId],
    queryFn: () => api<ImportJobPublic>(`/api/import/jobs/${jobId}`),
    refetchInterval: jobId ? 3000 : false,
    enabled: !!jobId,
  })

  const startJob = async () => {
    try {
      await post(`/api/import/jobs/${jobId}/start`)
      refetch()
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Failed to start job')
    }
  }

  if (!job) return <div className="text-slate-400">Loading…</div>

  const prog = job.progress as Record<string, unknown> | undefined
  const phase = (prog?.phase as string) || job.status
  const verification = prog?.verification as VerificationReport | undefined

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Import Job #{job.id}</h1>
        {job.status === 'queued' && <button onClick={startJob} className="rounded-md bg-emerald-600 px-4 py-2 text-sm hover:bg-emerald-500">Start Import</button>}
      </div>

      <section className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-3">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
          <div><span className="text-slate-400">Status:</span> <span className="ml-2 font-medium capitalize">{job.status}</span></div>
          <div><span className="text-slate-400">Phase:</span> <span className="ml-2 font-medium">{phase}</span></div>
          <div><span className="text-slate-400">Messages:</span> <span className="ml-2">{job.message_limit ?? 'all'}</span></div>
          <div><span className="text-slate-400">Created:</span> <span className="ml-2">{job.created_at}</span></div>
        </div>
        {job.error && <p className="text-sm text-rose-400">Error: {job.error}</p>}
      </section>

      {prog && Object.keys(prog).length > 0 && (
        <section className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-3">
          <h2 className="font-medium">Progress</h2>
          <pre className="text-xs text-slate-300 overflow-auto">{JSON.stringify(prog, null, 2)}</pre>
        </section>
      )}

      {verification && verification.overall && (
        <section className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-3">
          <h2 className="font-medium">Verification Report</h2>
          <p className="text-sm">Overall: <span className={verification.overall === 'FULL_MATCH' ? 'text-emerald-400' : verification.overall === 'PARTIAL' ? 'text-amber-400' : 'text-rose-400'}>{verification.overall}</span></p>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-2 text-sm">
            <div>Source: {verification.counts.source}</div>
            <div>Target: {verification.counts.target}</div>
            <div>Matched: {verification.counts.matched}</div>
            <div>Sender order: {verification.checks.sender_order ? '✓' : '✗'}</div>
            <div>Timestamp: {verification.checks.timestamp ? '✓' : '✗'}</div>
          </div>
          <details className="mt-2">
            <summary className="text-sm text-slate-400 cursor-pointer">View full report</summary>
            <pre className="mt-2 text-xs text-slate-300 overflow-auto">{JSON.stringify(verification, null, 2)}</pre>
          </details>
        </section>
      )}
    </div>
  )
}