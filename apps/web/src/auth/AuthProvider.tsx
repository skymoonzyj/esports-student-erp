import { useQueryClient } from "@tanstack/react-query"
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react"
import type { PropsWithChildren } from "react"

import {
  apiFetch,
  ProblemDetailsError,
  setCsrfToken,
  setInvalidSessionHandler,
} from "../api/client"

export type AuthUser = {
  account_id: string
  person_id: string
  full_name: string
  email: string
  force_password_change: boolean
  permissions: string[]
  scoped_permissions?: string[]
}

export type AuthStatus =
  | "loading"
  | "authenticated"
  | "anonymous"
  | "force_password_change"

type LoginResponse = {
  user: AuthUser
  csrf_token: string
}

type AuthContextValue = {
  status: AuthStatus
  user: AuthUser | null
  login: (identifier: string, password: string) => Promise<void>
  logout: () => Promise<void>
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>
  refreshUser: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

function statusFor(user: AuthUser): AuthStatus {
  return user.force_password_change ? "force_password_change" : "authenticated"
}

export function AuthProvider({ children }: PropsWithChildren) {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<AuthStatus>("loading")
  const [user, setUser] = useState<AuthUser | null>(null)

  const clearInvalidSession = useCallback(() => {
    setCsrfToken(null)
    queryClient.clear()
    setUser(null)
    setStatus("anonymous")
  }, [queryClient])

  useEffect(() => {
    setInvalidSessionHandler(clearInvalidSession)
    return () => setInvalidSessionHandler(null)
  }, [clearInvalidSession])

  useEffect(() => {
    let active = true
    async function restoreSession(): Promise<void> {
      try {
        const restored = await apiFetch<AuthUser>("/auth/me")
        const csrf = await apiFetch<{ csrf_token: string }>("/auth/csrf")
        if (!active) return
        setCsrfToken(csrf.csrf_token)
        setUser(restored)
        setStatus(statusFor(restored))
      } catch {
        if (!active) return
        setCsrfToken(null)
        setUser(null)
        setStatus("anonymous")
      }
    }
    void restoreSession()
    return () => {
      active = false
    }
  }, [])

  const login = useCallback(async (identifier: string, password: string) => {
    const result = await apiFetch<LoginResponse>("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ identifier, password }),
    })
    queryClient.clear()
    setCsrfToken(result.csrf_token)
    setUser(result.user)
    setStatus(statusFor(result.user))
  }, [queryClient])

  const logout = useCallback(async () => {
    const request = apiFetch<void>("/auth/logout", { method: "POST" })
    setCsrfToken(null)
    queryClient.clear()
    setUser(null)
    setStatus("anonymous")
    await request
  }, [queryClient])

  const refreshUser = useCallback(async () => {
    const fallbackStatus = user ? statusFor(user) : status
    setStatus("loading")
    queryClient.clear()
    try {
      const refreshed = await apiFetch<AuthUser>("/auth/me")
      setUser(refreshed)
      setStatus(statusFor(refreshed))
    } catch (error) {
      if (
        !(error instanceof ProblemDetailsError) ||
        error.problem.code !== "invalid_session"
      ) {
        setStatus(fallbackStatus)
      }
      throw error
    }
  }, [queryClient, status, user])

  const changePassword = useCallback(
    async (currentPassword: string, newPassword: string) => {
      const result = await apiFetch<LoginResponse>("/auth/change-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      })
      setCsrfToken(result.csrf_token)
      setUser(result.user)
      setStatus(statusFor(result.user))
    },
    [],
  )

  const value = useMemo<AuthContextValue>(
    () => ({ status, user, login, logout, changePassword, refreshUser }),
    [changePassword, login, logout, refreshUser, status, user],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext)
  if (!value) {
    throw new Error("useAuth must be used inside AuthProvider")
  }
  return value
}
