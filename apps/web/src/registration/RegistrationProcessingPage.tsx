import { NavLink, Navigate, Outlet } from "react-router-dom"
import { useAuth } from "../auth/AuthProvider"
import { useClaimAdminContext } from "../profileClaims/adminApi"
import { RegistrationAdminPage } from "./RegistrationAdminPage"
import "./registration.css"
import "./registrationProcessing.css"

export function RegistrationProcessingPage() {
  const { status, user } = useAuth()
  const context = useClaimAdminContext()
  const registration = user?.permissions.includes("registration.review") === true
  if (status !== "authenticated") return <p role="status">正在确认登录状态…</p>
  if (!registration && (!context.enabled || context.isSuccess && !context.isFetching && !context.data.can_enter)) return <Navigate to="/" replace />
  return <div className="registrationAdminPage">
    <nav aria-label="注册处理分类" className="registrationProcessingTabs">
      {registration ? <NavLink to="/settings/registrations" end>注册申请</NavLink> : null}
      {context.allowed ? <NavLink to="/settings/registrations/claims">档案认领</NavLink> : null}
    </nav>
    <Outlet />
  </div>
}

export function RegistrationApplicationsRoute() {
  const { user } = useAuth()
  const context = useClaimAdminContext()
  if (user?.permissions.includes("registration.review")) return <RegistrationAdminPage />
  if (context.allowed) return <Navigate to="/settings/registrations/claims" replace />
  if (context.enabled && (context.isPending || context.isFetching)) return <p role="status">正在确认认领审核权限…</p>
  return <Navigate to="/" replace />
}
