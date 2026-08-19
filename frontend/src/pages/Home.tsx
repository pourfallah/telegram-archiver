import { useQuery } from '@tanstack/react-query'

async function fetchHealth(): Promise<{ status: string; version: string; db: string }> {
  const resp = await fetch('/health')
  if (!resp.ok) throw new Error(`health check failed: ${resp.status}`)
  return resp.json()
}

export default function Home() {
  const { data, isLoading, isError } = useQuery({ queryKey: ['health'], queryFn: fetchHealth })

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col items-center justify-center gap-6">
      <h1 className="text-3xl font-semibold tracking-tight">
        Telegram Archive &amp; Migration Suite
      </h1>
      <p className="text-slate-400">
        Dashboard scaffolding is in place — full UI lands in Phase 6.
      </p>
      <div className="flex items-center gap-2 text-sm">
        <span
          className={`inline-block h-2.5 w-2.5 rounded-full ${
            data?.db === 'up' ? 'bg-emerald-400' : 'bg-rose-500'
          }`}
        />
        <span className="text-slate-300">
          API {isLoading ? 'checking…' : isError ? 'unreachable' : `${data!.status} (v${data!.version})`}
        </span>
      </div>
    </div>
  )
}
