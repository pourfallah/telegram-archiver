import { createContext, useContext, useState } from 'react'
import type { ReactNode } from 'react'
import { clearToken, getToken } from '../lib/api'

interface AuthCtx {
  token: string | null
  email: string | null
  login: (email: string, password: string) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthCtx>({
  token: null,
  email: null,
  login: async () => {},
  logout: () => {},
})

export function AuthProvider({ children }: { children: ReactNode }): JSX.Element {
  const [token, setTokenState] = useState<string | null>(getToken())
  const [email, setEmail] = useState<string | null>(localStorage.getItem('tasm_email'))

  const login = async (mail: string, password: string) => {
    const resp = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: mail, password }),
    })
    if (!resp.ok) {
      let msg = `login failed (${resp.status})`
      try {
        msg = (await resp.json()).detail?.message ?? msg
      } catch {
        /* ignore */
      }
      throw new Error(msg)
    }
    const data = await resp.json()
    localStorage.setItem('tasm_token', data.access_token)
    localStorage.setItem('tasm_email', mail)
    setTokenState(data.access_token)
    setEmail(mail)
  }

  const logout = () => {
    clearToken()
    localStorage.removeItem('tasm_email')
    setTokenState(null)
    setEmail(null)
  }

  return (
    <AuthContext.Provider value={{ token, email, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthCtx {
  return useContext(AuthContext)
}