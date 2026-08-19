import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, post } from '../lib/api'
import type { Account } from '../lib/types'

type Step = 'phone' | 'code' | '2fa'

export default function Accounts(): JSX.Element {
  const qc = useQueryClient()
  const { data: accounts } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => api<Account[]>('/api/accounts'),
    refetchInterval: 5000,
  })

  const [open, setOpen] = useState(false)
  const [step, setStep] = useState<Step>('phone')
  const [accountId, setAccountId] = useState<number | null>(null)
  const [phone, setPhone] = useState('')
  const [apiId, setApiId] = useState('')
  const [apiHash, setApiHash] = useState('')
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)

  const refresh = () => qc.invalidateQueries({ queryKey: ['accounts'] })

  const start = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    try {
      const acc = await post<Account>('/api/accounts', { phone, api_id: Number(apiId), api_hash: apiHash })
      setAccountId(acc.id)
      setStep('code')
      refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'failed to start login')
    }
  }

  const submitCode = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    try {
      const acc = await post<Account>(`/api/accounts/${accountId}/code`, { code })
      if (acc.status === 'auth_pending_2fa') setStep('2fa')
      else reset()
      refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'invalid code')
    }
  }

  const submit2fa = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    try {
      await post(`/api/accounts/${accountId}/2fa`, { password })
      reset()
      refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'invalid 2FA password')
    }
  }

  const remove = useMutation({
    mutationFn: (id: number) => api(`/api/accounts/${id}`, { method: 'DELETE' }),
    onSuccess: refresh,
  })

  const reset = () => {
    setOpen(false)
    setStep('phone')
    setAccountId(null)
    setPhone('')
    setApiId('')
    setApiHash('')
    setCode('')
    setPassword('')
  }

  const statusColor = (s: string) =>
    s === 'active' ? 'bg-emerald-500/20 text-emerald-300'
      : s === 'auth_pending_code' || s === 'auth_pending_2fa' ? 'bg-amber-500/20 text-amber-300'
      : 'bg-rose-500/20 text-rose-300'

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Telegram Accounts</h1>
        <button onClick={() => setOpen(true)} className="rounded-md bg-emerald-600 px-3 py-2 text-sm font-medium hover:bg-emerald-500">
          Add account
        </button>
      </div>

      {accounts?.length === 0 && <p className="text-slate-400">No accounts yet.</p>}
      <div className="grid gap-3 md:grid-cols-2">
        {accounts?.map((a) => (
          <div key={a.id} className="rounded-xl border border-slate-800 bg-slate-900 p-4">
            <div className="flex items-center justify-between">
              <span className="font-medium">{a.phone}</span>
              <span className={`rounded-full px-2 py-0.5 text-xs ${statusColor(a.status)}`}>{a.status}</span>
            </div>
            {a.last_error && <p className="mt-2 text-xs text-rose-400">{a.last_error}</p>}
            <div className="mt-3 flex gap-2">
              <button
                onClick={() => remove.mutate(a.id)}
                className="rounded-md border border-slate-700 px-2 py-1 text-xs hover:bg-slate-800"
              >
                Delete
              </button>
              <button
                onClick={() => post(`/api/accounts/${a.id}/check`).then(refresh).catch(() => {})}
                className="rounded-md border border-slate-700 px-2 py-1 text-xs hover:bg-slate-800"
              >
                Check
              </button>
            </div>
          </div>
        ))}
      </div>

      {open && (
        <div className="fixed inset-0 flex items-center justify-center bg-black/60 p-4">
          <form onSubmit={step === 'phone' ? start : step === 'code' ? submitCode : submit2fa}
            className="w-full max-w-md space-y-3 rounded-xl border border-slate-800 bg-slate-900 p-6">
            <h2 className="font-semibold">
              {step === 'phone' ? 'Add Telegram account' : step === 'code' ? 'Enter the OTP code' : 'Enter 2FA password'}
            </h2>
            {error && <p className="text-sm text-rose-400">{error}</p>}
            {step === 'phone' && (
              <>
                <input placeholder="+491234567890" value={phone} onChange={(e) => setPhone(e.target.value)}
                  className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm" required />
                <input placeholder="api_id" value={apiId} onChange={(e) => setApiId(e.target.value)}
                  className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm" required />
                <input placeholder="api_hash" value={apiHash} onChange={(e) => setApiHash(e.target.value)}
                  className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm" required />
              </>
            )}
            {step === 'code' && (
              <input placeholder="OTP code" value={code} onChange={(e) => setCode(e.target.value)}
                className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm" required />
            )}
            {step === '2fa' && (
              <input type="password" placeholder="2FA password" value={password} onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm" required />
            )}
            <div className="flex gap-2">
              <button type="submit" className="rounded-md bg-emerald-600 px-3 py-2 text-sm font-medium hover:bg-emerald-500">
                {step === 'phone' ? 'Send code' : 'Submit'}
              </button>
              <button type="button" onClick={reset} className="rounded-md border border-slate-700 px-3 py-2 text-sm hover:bg-slate-800">
                Cancel
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  )
}