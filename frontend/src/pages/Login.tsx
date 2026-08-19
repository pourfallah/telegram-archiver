import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

export default function Login(): JSX.Element {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(email, password)
      navigate('/')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'login failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950 text-slate-100">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-xl border border-slate-800 bg-slate-900 p-6">
        <h1 className="text-xl font-semibold">Sign in</h1>
        {error && <p className="text-sm text-rose-400">{error}</p>}
        <input
          type="email"
          placeholder="admin@example.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm"
          required
        />
        <input
          type="password"
          placeholder="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-full rounded-md border border-slate-700 bg-slate-800 px-3 py-2 text-sm"
          required
        />
        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-md bg-emerald-600 px-3 py-2 text-sm font-medium hover:bg-emerald-500 disabled:opacity-50"
        >
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}