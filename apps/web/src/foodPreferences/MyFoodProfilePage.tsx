import { useQueryClient } from "@tanstack/react-query"
import { useNavigate } from "react-router-dom"

import { useAuth } from "../auth/AuthProvider"
import { AccountIdentifiersPanel } from "../auth/AccountIdentifiersPanel"
import { portalKeys } from "../portal/api"
import { SelfProfilePanel } from "../people/SelfProfilePanel"
import { FoodPreferencesPanel } from "./FoodPreferencesPanel"

export function MyFoodProfilePage() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const navigate = useNavigate()

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
    <div className="portalPage foodProfilePage">
      <header className="portalPageHeader">
        <div>
          <p className="pageEyebrow">PROFILE</p>
          <h1>个人资料</h1>
          <p>查看本人资料、维护昵称、登录方式和餐饮偏好。</p>
        </div>
      </header>

      {user ? (
        <>
          <SelfProfilePanel personId={user.person_id} />
          <AccountIdentifiersPanel accountId={user.account_id} />
        </>
      ) : null}

      <FoodPreferencesPanel
        mode="self"
        intent="profile"
        onAccessRevoked={handleAccessRevoked}
      />
    </div>
  )
}
