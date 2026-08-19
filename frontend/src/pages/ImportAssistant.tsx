import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { api, post } from '../lib/api'
import type { ImportPackage, Instruction, ValidationResult } from '../lib/types'

export default function ImportAssistant(): JSX.Element {
  const { data: packages } = useQuery({
    queryKey: ['packages'],
    queryFn: () => api<ImportPackage[]>('/api/import/packages'),
  })

  const [packageId, setPackageId] = useState<number | ''>('')
  const [result, setResult] = useState<ValidationResult | null>(null)
  const [instructions, setInstructions] = useState<Instruction[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const validate = async () => {
    setError(null)
    setResult(null)
    try {
      setResult(await post<ValidationResult>('/api/import/validate', { package_id: Number(packageId) }))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'validation failed')
    }
  }

  const getInstructions = async () => {
    setError(null)
    try {
      const resp = await api<{ instructions: Instruction[] }>(`/api/import/${packageId}/instructions`)
      setInstructions(resp.instructions)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'instructions failed')
    }
  }

  const pkg = packages?.find((p) => p.id === packageId)

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Import Assistant</h1>

      <div className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-3">
        <div className="flex flex-wrap items-end gap-2">
          <div>
            <label className="block text-xs text-slate-400">Migration package</label>
            <select value={packageId} onChange={(e) => { setPackageId(Number(e.target.value)); setResult(null); setInstructions(null) }}
              className="rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm">
              <option value="">Select package…</option>
              {packages?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </div>
          <button onClick={validate} className="rounded-md bg-slate-700 px-3 py-2 text-sm hover:bg-slate-600">Validate</button>
          <button onClick={getInstructions} className="rounded-md bg-emerald-600 px-3 py-2 text-sm hover:bg-emerald-500">Instructions</button>
        </div>
        {error && <p className="text-sm text-rose-400">{error}</p>}
      </div>

      {result && (
        <section className="rounded-xl border border-slate-800 bg-slate-900 p-4 space-y-2">
          <h2 className="font-medium">
            Validation: <span className={result.validation_status === 'valid' ? 'text-emerald-400' : 'text-amber-400'}>{result.validation_status}</span>
          </h2>
          <p className="text-sm text-slate-300">
            {result.stats.messages} messages · {result.stats.media} media · {result.stats.users.length} users
            {result.stats.date_min ? ` · ${result.stats.date_min} → ${result.stats.date_max}` : ''}
          </p>
          {result.issues.length > 0 && (
            <ul className="list-disc pl-5 text-sm text-amber-300">
              {result.issues.map((i, idx) => <li key={idx}>{i}</li>)}
            </ul>
          )}
          <p className="text-sm text-slate-400">Users: {result.stats.users.join(', ') || 'n/a'}</p>
        </section>
      )}

      {pkg && !result && (
        <section className="rounded-xl border border-slate-800 bg-slate-900 p-4">
          <p className="text-sm text-slate-400">
            {pkg.messages_count} messages · {pkg.media_count} media · {pkg.users_detected ? Object.keys(pkg.users_detected).length : 0} users
          </p>
        </section>
      )}

      {instructions && (
        <section className="space-y-3">
          <h2 className="font-medium">Import instructions</h2>
          {instructions.map((i) => (
            <div key={i.step} className="rounded-xl border border-slate-800 bg-slate-900 p-4">
              <h3 className="font-medium">{i.step}. {i.title}</h3>
              <p className="mt-1 text-sm text-slate-300">{i.detail}</p>
            </div>
          ))}
        </section>
      )}
    </div>
  )
}