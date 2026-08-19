// Typed API client for the Telegram Archive backend.
// All routes (except login) require a Bearer token stored in localStorage.

const TOKEN_KEY = 'tasm_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}
export function setToken(t: string): void {
  localStorage.setItem(TOKEN_KEY, t)
}
export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : JSON.stringify(detail))
    this.status = status
    this.detail = detail
  }
}

export async function api<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  const resp = await fetch(path, { ...options, headers })
  if (resp.status === 401) {
    clearToken()
    window.location.href = '/login'
    throw new ApiError(401, 'unauthorized')
  }
  if (!resp.ok) {
    let detail: unknown = null
    try {
      detail = (await resp.json()).detail
    } catch {
      detail = resp.statusText
    }
    throw new ApiError(resp.status, detail)
  }
  if (resp.status === 204) return undefined as T
  return (await resp.json()) as T
}

export const post = <T = unknown>(path: string, body?: unknown) =>
  api<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
export const del = <T = unknown>(path: string) => api<T>(path, { method: 'DELETE' })