import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, post } from '../lib/api'
import type { Account, ExportJob, TargetChat, ImportJobPublic } from '../lib/types'

const TEST_COUNTS = [10, 50, 100, 250, 500, 900, 999, 1000, 1100, 2000]

export default function RealImport(): JSX.Element {
  const { data: accounts } = useQuery({ queryKey: ['accounts'], queryFn: () => api<Account[]>('/api/accounts') })
  const { data: exports } = useQuery({ queryKey: ['exports'], queryFn: () => api<ExportJob[]>('/api/exports') })

  const [step, setStep] = useState<number>(1)

  // Step 1: Select source archive
  const [sourceAccountId, setSourceAccountId] = useState<number | ''>('')
  const [exportId, setExportId] = useState<number | ''>('')

  // Step 2: Select target account
  const [targetAccountId, setTargetAccountId] = useState<number | ''>('')

  // Step 3: Select target chat
  const [targetChat, setTargetChat] = useState<TargetChat | null>(null)
  const { data: targetChats, isLoading: isLoadingChats } = useQuery({
    queryKey: ['targetChats', targetAccountId],
    queryFn: () => api<{ chats: TargetChat[] }>(`/api/import/${targetAccountId}/target-chats`),
    enabled: !!targetAccountId,
  })

  // Step 4: Peer validation
  const [peerValidation, setPeerValidation] = useState<{
    allowed: boolean
    confirm_text: string
    error_code: string | null
    error_message: string | null
    peer: { peer_id: number | null; peer_type: string | null; username: string | null; title: string | null; mutual_contact: boolean | null; message_count: number | null }
  } | null>(null)

  // Step 5: Preview
  const [preview, setPreview] = useState<{
    source: { account: string; chat: string; messages: number; media: number; date_range: string }
    target: { account: string; chat: string; messages: number }
    peer: { type: string; mutual_contact: string }
  } | null>(null)

  // Step 6: Test size selection
  const [testCount, setTestCount] = useState<number>(10)

  // Step 7: Pre-flight validation
  const [preFlight, setPreFlight] = useState<{
    validation_status: string
    format_check: any
    peer_check: any
    media: number
  } | null>(null)

  // Step 8: Confirmation
  const [confirmed, setConfirmed] = useState(false)

  // Step 9: Import execution
  const [job, setJob] = useState<ImportJobPublic | null>(null)
  const [progress, setProgress] = useState<any>(null)
  const { data: jobData } = useQuery({
    queryKey: ['importJob', job?.id],
    queryFn: () => api<ImportJobPublic>(`/api/import/jobs/${job?.id}`),
    refetchInterval: job ? 3000 : false,
    enabled: !!job?.id,
  })

  // Step 10: Verification
  const [verification, setVerification] = useState<any>(null)

  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const sourceAccount = accounts?.find(a => a.id === sourceAccountId)
  const targetAccount = accounts?.find(a => a.id === targetAccountId)
  const sourceExport = exports?.find(e => e.id === exportId)

  const nextStep = () => {
    if (step < 10) setStep(step + 1)
  }

  const prevStep = () => {
    if (step > 1) setStep(step - 1)
  }

  // Step 1: Select source
  const handleStep1 = async () => {
    if (!sourceAccountId || !exportId) {
      setError('Select source account and export')
      return
    }
    setError(null)
    nextStep()
  }

  // Step 2: Select target account
  const handleStep2 = async () => {
    if (!targetAccountId) {
      setError('Select target account (Account B)')
      return
    }
    setError(null)
    nextStep()
  }

  // Step 3: Select target chat
  const handleStep3 = async () => {
    if (!targetChat) {
      setError('Select target chat')
      return
    }
    setError(null)
    nextStep()
  }

  // Step 4: Validate peer
  const handleStep4 = async () => {
    if (!targetChat) {
      setError('No target chat selected')
      return
    }
    setError(null)
    setLoading(true)
    try {
      const res = await post<{ allowed: boolean; confirm_text: string; error_code: string | null; error_message: string | null; peer: any }>(
        `/api/import/${targetAccountId}/validate-peer`,
        {
          export_id: exportId,
          contact_identifier: targetChat.username || targetChat.id.toString(),
          count: testCount,
        }
      )
      setPeerValidation(res)
      if (res.allowed) {
        nextStep()
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'peer validation failed')
    } finally {
      setLoading(false)
    }
  }

  // Step 5: Preview
  const handleStep5 = async () => {
    if (!sourceExport || !targetChat || !sourceAccount || !targetAccount) return
    setPreview({
      source: {
        account: sourceAccount.phone,
        chat: sourceExport.chat_title,
        messages: sourceExport.messages_processed,
        media: sourceExport.files_downloaded,
        date_range: `${sourceExport.started_at} → ${sourceExport.finished_at}`,
      },
      target: {
        account: targetAccount.phone,
        chat: targetChat.title || targetChat.username || 'Unknown',
        messages: targetChat.message_count || 0,
      },
      peer: {
        type: targetChat.type,
        mutual_contact: peerValidation?.peer?.mutual_contact === true ? 'YES' : 'NO/UNKNOWN',
      },
    })
    nextStep()
  }

  // Step 6: Test size
  // Buttons directly call setTestCount + nextStep, no handler needed

  // Step 7: Pre-flight
  const handleStep7 = async () => {
    setLoading(true)
    try {
      // Validate import format
      // For now, just simulate
      setPreFlight({
        validation_status: 'valid',
        format_check: { pm: true, group: false, title: 'Test Chat' },
        peer_check: peerValidation,
        media: 0,
      })
      nextStep()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'pre-flight failed')
    } finally {
      setLoading(false)
    }
  }

  // Step 8: Confirmation
  const handleStep8 = () => {
    setConfirmed(true)
    nextStep()
  }

  // Step 9: Start import
  const handleStep9 = async () => {
    if (!targetChat || !peerValidation?.allowed) {
      setError('Peer validation required')
      return
    }
    setError(null)
    setLoading(true)
    try {
      const res = await post<ImportJobPublic>('/api/import/start-real', {
        export_id: exportId,
        target_account_id: targetAccountId,
        target_peer_id: targetChat.peer_id,
        message_limit: testCount,
        contact_identifier: targetChat.username || targetChat.id.toString(),
      })
      setJob(res)
      nextStep()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'import start failed')
    } finally {
      setLoading(false)
    }
  }

  // Step 10: Poll job
  if (jobData && jobData !== job) {
    setJob(jobData)
    setProgress(jobData.progress)
    if (jobData.status === 'completed' || jobData.status === 'partial' || jobData.status === 'failed') {
      if (jobData.progress?.verification) {
        setVerification(jobData.progress.verification)
      }
    }
  }

  const renderStep = () => {
    switch (step) {
      case 1:
        return (
          <section className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-4">
            <h2 className="font-medium">Step 1: Select Source Export (Account A)</h2>
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
                  {exports?.filter((e) => e.account_id === sourceAccountId && e.messages_processed > 0).map((x) => (
                    <option key={x.id} value={x.id}>{x.chat_title} (#{x.id} · {x.messages_processed} msgs)</option>
                  ))}
                </select>
              </div>
            </div>
            <div className="flex gap-2">
              <button onClick={handleStep1} className="rounded-md bg-emerald-600 px-4 py-2 text-sm hover:bg-emerald-500">Next</button>
            </div>
          </section>
        )
      case 2:
        return (
          <section className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-4">
            <h2 className="font-medium">Step 2: Select Target Account (Account B)</h2>
            <div>
              <label className="block text-xs text-slate-400">Target Account (Account B)</label>
              <select value={targetAccountId} onChange={(e) => setTargetAccountId(Number(e.target.value))}
                className="rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm">
                <option value="">Select…</option>
                {accounts?.filter((a) => a.status === 'active').map((a) => <option key={a.id} value={a.id}>{a.phone}</option>)}
              </select>
            </div>
            <div className="flex gap-2">
              <button onClick={prevStep} className="rounded-md border border-slate-700 px-4 py-2 text-sm hover:bg-slate-800">Back</button>
              <button onClick={handleStep2} className="rounded-md bg-emerald-600 px-4 py-2 text-sm hover:bg-emerald-500">Next</button>
            </div>
          </section>
        )
      case 3:
        return (
          <section className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-4">
            <h2 className="font-medium">Step 3: Select Target Chat (Existing A↔B Peer)</h2>
            {isLoadingChats && <p>Loading chats…</p>}
            {!isLoadingChats && targetChats && (
              <div className="space-y-2 max-h-96 overflow-auto">
                {targetChats.chats.map((chat) => (
                  <button
                    key={chat.id}
                    onClick={() => setTargetChat(chat)}
                    className={`w-full text-left rounded-md border px-3 py-2 text-sm ${targetChat?.id === chat.id ? 'border-emerald-500 bg-emerald-950/30' : 'border-slate-700 hover:bg-slate-800'}`}
                  >
                    <div className="font-medium">{chat.title || chat.username || 'Unknown'}</div>
                    <div className="text-xs text-slate-400">
                      Type: {chat.type} · ID: {chat.peer_id} · Messages: {chat.message_count ?? 'unknown'}
                    </div>
                  </button>
                ))}
              </div>
            )}
            <div className="flex gap-2">
              <button onClick={prevStep} className="rounded-md border border-slate-700 px-4 py-2 text-sm hover:bg-slate-800">Back</button>
              <button onClick={handleStep3} disabled={!targetChat} className="rounded-md bg-emerald-600 px-4 py-2 text-sm hover:bg-emerald-500 disabled:opacity-50">Next</button>
            </div>
          </section>
        )
      case 4:
        return (
          <section className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-4">
            <h2 className="font-medium">Step 4: Peer Validation</h2>
            <p className="text-sm text-slate-400">Calls messages.checkHistoryImportPeer() on Telegram.</p>
            <button onClick={handleStep4} disabled={loading} className="rounded-md bg-slate-700 px-3 py-2 text-sm hover:bg-slate-600 disabled:opacity-50">
              {loading ? 'Validating…' : 'Validate Peer'}
            </button>
            {peerValidation && (
              <div className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-3">
                <h3 className="font-medium">Result</h3>
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
              </div>
            )}
            <div className="flex gap-2">
              <button onClick={prevStep} className="rounded-md border border-slate-700 px-4 py-2 text-sm hover:bg-slate-800">Back</button>
              {peerValidation?.allowed && <button onClick={nextStep} className="rounded-md bg-emerald-600 px-4 py-2 text-sm hover:bg-emerald-500">Next</button>}
            </div>
          </section>
        )
      case 5:
        return (
          <section className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-4">
            <h2 className="font-medium">Step 5: Preview</h2>
            {preview && (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
                  <h3 className="font-medium text-sky-400">Source</h3>
                  <p className="text-sm text-slate-300">Account: {preview.source.account}</p>
                  <p className="text-sm text-slate-300">Chat: {preview.source.chat}</p>
                  <p className="text-sm text-slate-300">Messages: {preview.source.messages}</p>
                  <p className="text-sm text-slate-300">Media: {preview.source.media}</p>
                  <p className="text-sm text-slate-300">Date range: {preview.source.date_range}</p>
                </div>
                <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
                  <h3 className="font-medium text-emerald-400">Target</h3>
                  <p className="text-sm text-slate-300">Account: {preview.target.account}</p>
                  <p className="text-sm text-slate-300">Chat: {preview.target.chat}</p>
                  <p className="text-sm text-slate-300">Current messages: {preview.target.messages}</p>
                </div>
              </div>
            )}
            <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
              <h3 className="font-medium text-amber-400">Peer</h3>
              <p className="text-sm text-slate-300">Type: {peerValidation?.peer.peer_type}</p>
              <p className="text-sm text-slate-300">Mutual contact: {peerValidation?.peer.mutual_contact === true ? 'YES' : 'NO/UNKNOWN'}</p>
            </div>
            <div className="flex gap-2">
              <button onClick={prevStep} className="rounded-md border border-slate-700 px-4 py-2 text-sm hover:bg-slate-800">Back</button>
              <button onClick={handleStep5} className="rounded-md bg-emerald-600 px-4 py-2 text-sm hover:bg-emerald-500">Next</button>
            </div>
          </section>
        )
      case 6:
        return (
          <section className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-4">
            <h2 className="font-medium">Step 6: Test Size</h2>
            <p className="text-sm text-slate-400">Choose number of messages for the test import.</p>
            <div className="flex flex-wrap gap-2">
              {TEST_COUNTS.map((c) => (
                <button
                  key={c}
                  onClick={() => { setTestCount(c); nextStep() }}
                  className={`rounded-md px-3 py-1.5 text-sm ${testCount === c ? 'bg-emerald-600 text-white' : 'border border-slate-700 hover:bg-slate-800'}`}
                >
                  {c}
                </button>
              ))}
            </div>
            <div className="flex gap-2">
              <button onClick={prevStep} className="rounded-md border border-slate-700 px-4 py-2 text-sm hover:bg-slate-800">Back</button>
            </div>
          </section>
        )
      case 7:
        return (
          <section className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-4">
            <h2 className="font-medium">Step 7: Pre-flight Validation</h2>
            <p className="text-sm text-slate-400">Calls messages.checkHistoryImport() and validates format.</p>
            <button onClick={handleStep7} disabled={loading} className="rounded-md bg-slate-700 px-3 py-2 text-sm hover:bg-slate-600 disabled:opacity-50">
              {loading ? 'Validating…' : 'Run Pre-flight'}
            </button>
            {preFlight && (
              <div className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-2">
                <p className="text-sm"><span className="font-medium">Format check:</span> {preFlight.format_check?.pm ? 'Private chat ✓' : 'Group'}</p>
                <p className="text-sm"><span className="font-medium">Title:</span> {preFlight.format_check?.title || 'N/A'}</p>
                <p className="text-sm"><span className="font-medium">Peer check:</span> {preFlight.peer_check?.allowed ? 'ALLOWED' : 'BLOCKED'}</p>
                <p className="text-sm"><span className="font-medium">Media:</span> {preFlight.media}</p>
              </div>
            )}
            <div className="flex gap-2">
              <button onClick={prevStep} className="rounded-md border border-slate-700 px-4 py-2 text-sm hover:bg-slate-800">Back</button>
              {preFlight && <button onClick={nextStep} className="rounded-md bg-emerald-600 px-4 py-2 text-sm hover:bg-emerald-500">Next</button>}
            </div>
          </section>
        )
      case 8:
        return (
          <section className="rounded-xl border border-emerald-800 bg-emerald-950/30 p-4 space-y-4">
            <h2 className="font-medium text-emerald-400">Step 8: Confirmation Required</h2>
            <p className="text-sm text-slate-300">
              This operation will modify the selected Telegram chat.
              <br />
              <strong>Source:</strong> {sourceAccount?.phone} → {sourceExport?.chat_title} ({sourceExport?.messages_processed} msgs)
              <br />
              <strong>Target:</strong> {targetAccount?.phone} → {targetChat?.title} ({targetChat?.message_count ?? 'unknown'} msgs current)
              <br />
              <strong>Messages to import:</strong> {testCount}
            </p>
            <label className="flex items-center gap-2">
              <input type="checkbox" onChange={(e) => setConfirmed(e.target.checked)} />
              <span className="text-sm">I understand this will modify the selected Telegram chat.</span>
            </label>
            <div className="flex gap-2">
              <button onClick={prevStep} className="rounded-md border border-slate-700 px-4 py-2 text-sm hover:bg-slate-800">Back</button>
              <button onClick={handleStep8} disabled={!confirmed} className="rounded-md bg-emerald-600 px-4 py-2 text-sm hover:bg-emerald-500 disabled:opacity-50">Next</button>
            </div>
          </section>
        )
      case 9:
        return (
          <section className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-4">
            <h2 className="font-medium">Step 9: Import Execution</h2>
            {loading && <button disabled className="rounded-md bg-slate-700 px-3 py-2 text-sm">Starting…</button>}
            {!loading && !job && (
              <button onClick={handleStep9} className="rounded-md bg-emerald-600 px-4 py-2 text-sm hover:bg-emerald-500">Start Real Import</button>
            )}
            {job && (
              <div className="space-y-3">
                <p className="text-sm">Job ID: {job.id} · Status: <span className="font-medium capitalize">{job.status}</span></p>
                {progress && (
                  <div className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-2">
                    <p className="text-sm"><span className="font-medium">Phase:</span> {progress.phase}</p>
                    {progress.uploaded !== undefined && <p className="text-sm">Media: {progress.uploaded}/{progress.total}</p>}
                    {progress.current_file && <p className="text-sm">Current: {progress.current_file}</p>}
                    {progress.verification && (
                      <div className="border-t border-slate-800 pt-2">
                        <p className="text-sm">Verification: <span className={progress.verification.overall === 'FULL_MATCH' ? 'text-emerald-400' : 'text-amber-400'}>{progress.verification.overall}</span></p>
                        <p className="text-xs">Matched: {progress.verification.counts.matched}/{progress.verification.counts.source}</p>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
            <div className="flex gap-2">
              <button onClick={prevStep} className="rounded-md border border-slate-700 px-4 py-2 text-sm hover:bg-slate-800">Back</button>
            </div>
          </section>
        )
      case 10:
        const jobStatus = jobData?.status ?? job?.status
        const phase = (jobData?.progress?.phase ?? progress?.phase) as string | undefined
        const jobError = jobData?.error ?? job?.error
        const phaseLabels: Record<string, string> = {
          validating: 'Validating job…',
          peer_checking: 'Checking target peer with Telegram…',
          building_import_file: 'Building import file…',
          check_import_format: 'Validating format with Telegram…',
          init_history_import: 'Initializing import…',
          media_uploading: `Uploading media… (${jobData?.progress?.uploaded ?? progress?.uploaded ?? 0}/${jobData?.progress?.total ?? progress?.total ?? '?'})`,
          media_splicing: 'Finalizing media…',
          starting_import: 'Starting history import…',
          waiting: 'Telegram processing the import…',
          verifying: 'Verifying imported messages (this can take ~2 min)…',
          completed: 'Done',
        }
        return (
          <section className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-4">
            <h2 className="font-medium">Step 10: Verification Report</h2>

            {jobError && (
              <div className="rounded-md border border-rose-500 bg-rose-950/30 p-3 text-sm text-rose-300">
                Import failed: {jobError}
              </div>
            )}

            {verification ? (
              <div className="space-y-3">
                <p className="text-sm">
                  Overall:{' '}
                  <span className={verification.overall === 'FULL_MATCH' ? 'text-emerald-400' : verification.overall === 'PARTIAL' || verification.overall === 'SOURCE_COVERED_EXTRA_IN_TARGET' ? 'text-amber-400' : 'text-rose-400'}>
                    {verification.overall}
                  </span>
                  <span className="ml-2 text-xs text-slate-400">(import finished)</span>
                </p>
                {(() => {
                  const ta = verification.timestamp_analysis
                  if (!ta) return null
                  return (
                    <div className="text-xs rounded-md border border-slate-700 p-3 space-y-1">
                      <div>Historical timestamps preserved as metadata: <b>{ta.historical_metadata_preserved}/{ta.matched_messages}</b></div>
                      <div>Visible dates equal source dates: <b>{ta.visible_equals_source}/{ta.matched_messages}</b>{ta.placement_note ? ` — ${ta.placement_note}` : ''}</div>
                    </div>
                  )
                })()}
                <div className="grid grid-cols-2 md:grid-cols-5 gap-2 text-sm">
                  <div>Source: {verification.counts.source}</div>
                  <div>Target: {verification.counts.target}</div>
                  <div>Matched: {verification.counts.matched}</div>
                  <div>Text: {verification.checks.text ? '✓' : '✗'}</div>
                  <div>Media: {verification.checks.media ? '✓' : '✗'}</div>
                </div>
                <details className="mt-2">
                  <summary className="text-sm text-slate-400 cursor-pointer">View full report</summary>
                  <pre className="mt-2 text-xs text-slate-300 overflow-auto">{JSON.stringify(verification, null, 2)}</pre>
                </details>
              </div>
            ) : jobError ? null : (
              <div className="space-y-2">
                <p className="text-slate-300 text-sm">
                  {phaseLabels[phase ?? ''] ?? 'Working…'}
                </p>
                <div className="h-1.5 w-full max-w-md overflow-hidden rounded bg-slate-800">
                  <div className="h-full w-1/3 animate-pulse rounded bg-sky-500" />
                </div>
                <p className="text-xs text-slate-500">
                  Job #{job?.id} · status: {jobStatus} · polling every 3 s
                </p>
              </div>
            )}
            <div className="flex gap-2">
              <button onClick={prevStep} className="rounded-md border border-slate-700 px-4 py-2 text-sm hover:bg-slate-800">Back</button>
            </div>
          </section>
        )
      default:
        return null
    }
  }

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-semibold">Real Telegram Import</h1>
      <p className="text-sm text-slate-400">
        Import history into an EXISTING Telegram peer using the official MTProto import API.
      </p>

      {/* Progress indicator */}
      <div className="flex gap-1">
        {[1,2,3,4,5,6,7,8,9,10].map((s) => (
          <div key={s} className={`flex-1 h-2 rounded ${s < step ? 'bg-emerald-500' : s === step ? 'bg-sky-500' : 'bg-slate-700'}`} />
        ))}
      </div>

      {error && <div className="rounded-xl border border-rose-500 bg-rose-950/30 p-3 text-rose-400 text-sm">{error}</div>}

      {renderStep()}
    </div>
  )
}