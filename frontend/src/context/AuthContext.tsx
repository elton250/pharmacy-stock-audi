import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { api, ApiError } from '../lib/api'
import type { UserOut } from '../lib/types'

interface AuthState {
  user: UserOut | null
  loading: boolean
  setupAvailable: boolean
  permissions: Set<string>
  can: (perm: string) => boolean
  login: (username: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refresh: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<UserOut | null>(null)
  const [loading, setLoading] = useState(true)
  const [setupAvailable, setSetupAvailable] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const me = await api.get<UserOut>('/api/auth/me')
      setUser(me)
      setSetupAvailable(false)
    } catch (err) {
      setUser(null)
      if (err instanceof ApiError && err.status === 401) {
        try {
          const res = await api.get<{ available: boolean }>('/api/auth/setup-available')
          setSetupAvailable(res.available)
        } catch {
          setSetupAvailable(false)
        }
      }
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const login = useCallback(
    async (username: string, password: string) => {
      const res = await api.post<{ user: UserOut }>('/api/auth/login', { username, password })
      setUser(res.user)
      setSetupAvailable(false)
    },
    [],
  )

  const logout = useCallback(async () => {
    await api.post('/api/auth/logout')
    setUser(null)
  }, [])

  const permissions = useMemo(() => {
    const set = new Set<string>()
    for (const role of user?.roles ?? []) {
      for (const p of role.permissions) set.add(p)
    }
    return set
  }, [user])

  const can = useCallback((perm: string) => permissions.has(perm), [permissions])

  const value: AuthState = { user, loading, setupAvailable, permissions, can, login, logout, refresh }
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
