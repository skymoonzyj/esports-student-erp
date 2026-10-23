import { useQueryClient } from "@tanstack/react-query"
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import type { PropsWithChildren } from "react"

import {
  apiFetch,
  ProblemDetailsError,
  setCsrfToken,
  setInvalidSessionHandler,
} from "../api/client"
import {
  setRegistrationCsrfToken,
  setRegistrationSessionBoundaryHandler,
} from "../registration/api"
import { registrationKeys } from "../registration/keys"
import {
  clearTalentPending,
  clearTalentPendingExcept,
} from "../talentTests/pending"
import {
  clearAssessmentPending,
  clearAssessmentPendingExcept,
} from "../esportsAssessments/pending"
import { clearAllAccountIdentifierChallengeDrafts } from "./accountIdentifierChallengeStorage"

export type AuthUser = {
  account_id: string
  person_id: string
  full_name: string
  email: string | null
  force_password_change: boolean
  is_super_admin?: boolean
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
  refreshUser: (options?: { background?: boolean }) => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

function statusFor(user: AuthUser): AuthStatus {
  return user.force_password_change ? "force_password_change" : "authenticated"
}

export function AuthProvider({ children }: PropsWithChildren) {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<AuthStatus>("loading")
  const [user, setUser] = useState<AuthUser | null>(null)
  const restoreSessionPromise = useRef<Promise<LoginResponse> | null>(null)
  const pendingLogout = useRef<Promise<void>>(Promise.resolve())
  const authGeneration = useRef(0)

  const clearInvalidSession = useCallback(() => {
    authGeneration.current += 1
    setCsrfToken(null)
    setRegistrationCsrfToken(null)
    clearAllAccountIdentifierChallengeDrafts()
    queryClient.clear()
    setUser(null)
    setStatus("anonymous")
  }, [queryClient])

  useEffect(() => {
    setInvalidSessionHandler(clearInvalidSession)
    return () => setInvalidSessionHandler(null)
  }, [clearInvalidSession])

  useEffect(() => {
    const clearRegistrationBoundary = () => {
      const root = registrationKeys.root()
      const cancellation = queryClient.cancelQueries({ queryKey: root })
      queryClient.removeQueries({ queryKey: root })
      void cancellation.finally(() => queryClient.removeQueries({ queryKey: root }))
    }
    setRegistrationSessionBoundaryHandler(clearRegistrationBoundary)
    return () => setRegistrationSessionBoundaryHandler(null)
  }, [queryClient])

  useEffect(() => {
    let active = true
    if (!restoreSessionPromise.current) {
      restoreSessionPromise.current = (async () => {
        const restored = await apiFetch<AuthUser>("/auth/me", {
          headers: { "X-ERP-Session-Restore": "1" },
        })
        const csrf = await apiFetch<{ csrf_token: string }>("/auth/csrf")
        return { user: restored, csrf_token: csrf.csrf_token }
      })()
    }
    async function restoreSession(): Promise<void> {
      try {
        const restored = await restoreSessionPromise.current!
        if (!active) return
        setCsrfToken(restored.csrf_token)
        clearTalentPendingExcept(restored.user.account_id)
        clearAssessmentPendingExcept(restored.user.account_id)
        setUser(restored.user)
        setStatus(statusFor(restored.user))
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
    const generation = authGeneration.current + 1
    authGeneration.current = generation
    while (true) {
      const logoutBarrier = pendingLogout.current
      await logoutBarrier
      if (authGeneration.current !== generation) return
      if (pendingLogout.current === logoutBarrier) break
    }
    const result = await apiFetch<LoginResponse>("/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ identifier, password }),
    })
    if (authGeneration.current !== generation) return
    queryClient.clear()
    setCsrfToken(result.csrf_token)
    setRegistrationCsrfToken(null)
    clearAllAccountIdentifierChallengeDrafts()
    clearTalentPendingExcept(result.user.account_id)
    clearAssessmentPendingExcept(result.user.account_id)
    setUser(result.user)
    setStatus(statusFor(result.user))
  }, [queryClient])

  const logout = useCallback(async () => {
    const request = apiFetch<void>("/auth/logout", { method: "POST" })
    const settledRequest = request.catch(() => undefined)
    const logoutBarrier = Promise.all([
      pendingLogout.current,
      settledRequest,
    ]).then(() => undefined)
    pendingLogout.current = logoutBarrier
    authGeneration.current += 1
    setCsrfToken(null)
    setRegistrationCsrfToken(null)
    clearAllAccountIdentifierChallengeDrafts()
    clearTalentPending()
    clearAssessmentPending()
    queryClient.clear()
    setUser(null)
    setStatus("anonymous")
    await logoutBarrier
  }, [queryClient])

  const refreshUser = useCallback(async (
    { background = false }: { background?: boolean } = {},
  ) => {
    const generation = authGeneration.current
    const fallbackStatus = user ? statusFor(user) : status
    if (!background) setStatus("loading")
    queryClient.clear()
    try {
      const refreshed = await apiFetch<AuthUser>("/auth/me")
      if (authGeneration.current !== generation) return
      setUser(refreshed)
      setStatus(statusFor(refreshed))
    } catch (error) {
      if (
        !background &&
        authGeneration.current === generation &&
        (!(error instanceof ProblemDetailsError) ||
          error.problem.code !== "invalid_session")
      ) {
        setStatus(fallbackStatus)
      }
      throw error
    }
  }, [queryClient, status, user])

  const changePassword = useCallback(
    async (currentPassword: string, newPassword: string) => {
      const generation = authGeneration.current
      const result = await apiFetch<LoginResponse>("/auth/change-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      })
      if (authGeneration.current !== generation) return
      setCsrfToken(result.csrf_token)
      clearAllAccountIdentifierChallengeDrafts()
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

export function useOptionalAuth(): AuthContextValue | null {
  return useContext(AuthContext)
}
