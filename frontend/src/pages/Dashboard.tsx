import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { Stats } from '../lib/types'

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`
  return `${(n / 1024 ** 3).toFixed(2)} GB`
}

function Card({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900 p-5">
      <p className="text-sm text-slate-400">{label}</p>
      <p className="mt-1 text-2xl font-semibold">{value}</p>
    </div>
  )
}

export default function Dashboard(): JSX.Element {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['stats'],
    queryFn: () => api<Stats>('/api/stats'),
    refetchInterval: 5000,
  })

  if (isLoading) return <p className="text-slate-400">Loading…</p>
  if (isError) return <p className="text-rose-400">Could not load dashboard stats.</p>

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Dashboard</h1>
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Card label="Telegram accounts" value={data!.accounts} />
        <Card label="Total exports" value={data!.exports_total} />
        <Card label="Running jobs" value={data!.exports_running} />
        <Card label="Storage used" value={fmtBytes(data!.storage_bytes)} />
      </div>
      <p className="text-sm text-slate-400">
        Add a Telegram account, search a chat, and start an export from the{' '}
        <span className="text-slate-200">Accounts</span> / <span className="text-slate-200">Exports</span> pages.
      </p>
    </div>
  )
}