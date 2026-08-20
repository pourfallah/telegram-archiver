import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'

const links = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/accounts', label: 'Accounts' },
  { to: '/exports', label: 'Exports' },
  { to: '/migration', label: 'Migration' },
  { to: '/test-import', label: 'Test Import' },
  { to: '/real-import', label: 'Real Import' },
  { to: '/import', label: 'Import Assistant' },
]

export default function Layout(): JSX.Element {
  const { email, logout } = useAuth()
  const navigate = useNavigate()

  const handleLogout = () => {
    logout()
    navigate('/login')
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 bg-slate-900/60">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
          <span className="font-semibold tracking-tight">
            Telegram Archive &amp; Migration
          </span>
          <div className="flex items-center gap-4">
            {email && <span className="text-sm text-slate-400">{email}</span>}
            <button onClick={handleLogout} className="text-sm text-slate-400 hover:text-white">
              Log out
            </button>
          </div>
        </div>
        <nav className="mx-auto flex max-w-6xl gap-1 px-4 pb-2">
          {links.map((l) => (
            <NavLink
              key={l.to}
              to={l.to}
              end={l.end}
              className={({ isActive }) =>
                `rounded-md px-3 py-1.5 text-sm ${
                  isActive ? 'bg-slate-700 text-white' : 'text-slate-400 hover:text-white'
                }`
              }
            >
              {l.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Outlet />
      </main>
    </div>
  )
}