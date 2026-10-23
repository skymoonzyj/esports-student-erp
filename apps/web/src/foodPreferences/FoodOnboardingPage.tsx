import { useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { useNavigate } from "react-router-dom"

import { useAuth } from "../auth/AuthProvider"
import { portalKeys } from "../portal/api"
import { FoodPreferencesPanel } from "./FoodPreferencesPanel"

export { foodOnboardingPath } from "./paths"

export function FoodOnboardingPage() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [refreshing, setRefreshing] = useState(false)
  const [refreshError, setRefreshError] = useState<string | null>(null)

  async function finishOnboarding(): Promise<void> {
    if (!user || refreshing) return
    setRefreshing(true)
    setRefreshError(null)
    try {
      await queryClient.refetchQueries(
        {
          queryKey: portalKeys.context(user.account_id),
          exact: true,
          type: "active",
        },
        { throwOnError: true },
      )
      navigate("/", { replace: true })
    } catch {
      setRefreshError("餐饮偏好已保存，但暂时无法刷新待办，请重试。")
    } finally {
      setRefreshing(false)
    }
  }

  function handleAccessRevoked(): void {
    if (user) {
      void queryClient
        .refetchQueries({
          queryKey: portalKeys.context(user.account_id),
          exact: true,
          type: "active",
        })
        .catch(() => undefined)
    }
    navigate("/", { replace: true })
  }

  return (
    <div className="portalPage foodOnboardingPage">
      <header className="portalPageHeader">
        <div>
          <p className="pageEyebrow">FOOD PROFILE</p>
          <h1>完善餐饮偏好</h1>
          <p>请填写喜欢的菜系和最喜欢的菜，方便营期餐饮安排。</p>
        </div>
      </header>

      <FoodPreferencesPanel
        mode="self"
        intent="onboarding"
        onSkipped={() => navigate("/", { replace: true })}
        onCommitted={() => void finishOnboarding()}
        onAccessRevoked={handleAccessRevoked}
      />

      {refreshing ? <p aria-live="polite">正在刷新待办…</p> : null}
      {refreshError ? <p role="alert">{refreshError}</p> : null}
    </div>
  )
}
