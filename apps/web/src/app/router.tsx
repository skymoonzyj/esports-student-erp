import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useMemo } from "react"
import type { ReactNode } from "react"
import {
  createBrowserRouter,
  Navigate,
  Outlet,
  RouterProvider,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom"
import type { RouteObject } from "react-router-dom"

import { AdmissionCasesPage } from "../admissions/AdmissionCasesPage"
import { EnrollmentDetailPage } from "../admissions/EnrollmentDetailPage"
import { EnrollmentsPage } from "../admissions/EnrollmentsPage"
import { PaymentsPage } from "../admissions/PaymentsPage"
import { useAuth } from "../auth/AuthProvider"
import { ChangePasswordPage, LoginPage } from "../auth/LoginPage"
import { RegistrationPage } from "../auth/RegistrationPage"
import { AppShell } from "../layout/AppShell"
import { EvaluationCenterPage } from "../evaluations/EvaluationCenterPage"
import { EvaluationConnectPage } from "../evaluations/EvaluationConnectPage"
import { EvaluationAdminPage } from "../evaluations/EvaluationAdminPage"
import { EvaluationAuthorizationProvider } from "../evaluations/EvaluationAuthorization"
import { GameAccountsPage } from "../evaluations/GameAccountsPage"
import { SystemEvaluationPage } from "../evaluations/SystemEvaluationPage"
import { useEvaluationAccessError } from "../evaluations/useEvaluationAccessError"
import { useEvaluationContext } from "../evaluations/useEvaluationContext"
import { HousingOverviewPage } from "../housing/HousingOverviewPage"
import { HousingReportsPage } from "../housing/HousingReportsPage"
import { HousingRoomDetailPage } from "../housing/HousingRoomDetailPage"
import { MyHousingPage } from "../housing/MyHousingPage"
import { housingKeys } from "../housing/housingKeys"
import { reportingKeys } from "../housing/reportingKeys"
import { PeoplePage } from "../people/PeoplePage"
import { PersonDetailPage } from "../people/PersonDetailPage"
import { PersonForm } from "../people/PersonForm"
import { CoachNextCohortsPage } from "../portal/CoachNextCohortsPage"
import { LearningPage } from "../portal/LearningPage"
import { MyPaymentsPage } from "../portal/MyPaymentsPage"
import type { PortalCapability } from "../portal/api"
import { portalKeys } from "../portal/api"
import { PortalDashboardPage } from "../portal/PortalDashboardPage"
import { usePortalAccessError } from "../portal/usePortalAccessError"
import { usePortalContext } from "../portal/usePortalContext"
import { AccountsPage } from "../settings/AccountsPage"
import { AuditPage } from "../settings/AuditPage"
import { MailSettingsPage } from "../settings/MailSettingsPage"
import { PermissionsPage } from "../settings/PermissionsPage"
import { CohortDetailPage } from "../teaching/CohortDetailPage"
import { CohortsPage } from "../teaching/CohortsPage"
import { TeachingCatalogPage } from "../teaching/TeachingCatalogPage"

function LoadingScreen() {
  return (
    <main className="routeLoading" aria-live="polite">
      <span className="routeLoadingMark">竞</span>
      <p>正在安全连接工作台…</p>
    </main>
  )
}

type LoginRouteState = { returnTo?: unknown }

const EVALUATION_CONNECT_PATH =
  /^\/evaluation-connect\/[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

function safeLoginReturn(state: unknown): string {
  if (!state || typeof state !== "object") return "/"
  const returnTo = (state as LoginRouteState).returnTo
  return typeof returnTo === "string" && EVALUATION_CONNECT_PATH.test(returnTo)
    ? returnTo
    : "/"
}

function PortalRouteLoading() {
  return (
    <div className="portalRouteLoading" aria-live="polite">
      <span className="routeLoadingMark">竞</span>
      <p>正在安全连接工作台…</p>
    </div>
  )
}

function LoginBoundary() {
  const { status } = useAuth()
  const location = useLocation()
  const returnTo = safeLoginReturn(location.state)
  if (status === "loading") return <LoadingScreen />
  if (status === "authenticated") return <Navigate to={returnTo} replace />
  if (status === "force_password_change") {
    return (
      <Navigate
        to="/change-password"
        state={{ returnTo }}
        replace
      />
    )
  }
  return <LoginPage />
}

function PasswordBoundary() {
  const { status } = useAuth()
  const location = useLocation()
  const returnTo = safeLoginReturn(location.state)
  if (status === "loading") return <LoadingScreen />
  if (status === "anonymous") {
    return <Navigate to="/login" state={{ returnTo }} replace />
  }
  return <ChangePasswordPage returnTo={returnTo} />
}

function EvaluationConnectBoundary() {
  const { status } = useAuth()
  const location = useLocation()
  if (status === "loading") return <LoadingScreen />
  if (status === "anonymous") {
    return (
      <Navigate
        to="/login"
        state={{ returnTo: location.pathname }}
        replace
      />
    )
  }
  if (status === "force_password_change") {
    return (
      <Navigate
        to="/change-password"
        state={{ returnTo: location.pathname }}
        replace
      />
    )
  }
  return <EvaluationConnectPage />
}

function AuthenticatedLayout() {
  const { status, user, logout } = useAuth()
  const portalContext = usePortalContext()
  const permissions = useMemo(() => {
    const visible = new Set(user?.permissions ?? [])
    for (const permission of user?.scoped_permissions ?? []) {
      if (
        [
          "permissions.manage",
          "teaching.read",
          "admission_cases.read",
          "enrollments.read",
          "housing.read",
          "housing.reports.export_sensitive",
        ].includes(permission)
      ) {
        visible.add(permission)
      }
    }
    return visible
  }, [user?.permissions, user?.scoped_permissions])
  if (status === "loading") return <LoadingScreen />
  if (status === "anonymous") return <Navigate to="/login" replace />
  if (status === "force_password_change") {
    return <Navigate to="/change-password" replace />
  }
  return (
    <AppShell
      permissions={permissions}
      globalPermissions={new Set(user?.permissions ?? [])}
      portalContext={
        portalContext.data && !portalContext.isFetching
          ? portalContext.data
          : undefined
      }
      userName={user?.full_name}
      onLogout={() => void logout()}
    >
      <Outlet />
    </AppShell>
  )
}

function PeopleDirectoryRoute() {
  const { user } = useAuth()
  const permissions = new Set(user?.permissions ?? [])
  if (!permissions.has("people.list")) return <Navigate to="/" replace />
  return <PeoplePage canCreate={permissions.has("people.create")} />
}

function NewPersonRoute() {
  const { user } = useAuth()
  if (!user?.permissions.includes("people.create")) return <Navigate to="/" replace />
  return <PersonForm />
}

function EditPersonRoute() {
  const { user } = useAuth()
  const { personId } = useParams()
  if (!user?.permissions.includes("people.update") || !personId) {
    return <Navigate to="/" replace />
  }
  return <PersonForm personId={personId} />
}

function PersonDetailRoute() {
  const { user } = useAuth()
  return <PersonDetailPage permissions={new Set(user?.permissions ?? [])} />
}

function hasEffectivePermission(
  user: ReturnType<typeof useAuth>["user"],
  permission: string,
): boolean {
  return Boolean(
    user?.permissions.includes(permission)
    || user?.scoped_permissions?.includes(permission),
  )
}

function PermissionRoute({ permission, children }: { permission: string; children: ReactNode }) {
  const { user } = useAuth()
  if (!hasEffectivePermission(user, permission)) return <Navigate to="/" replace />
  return children
}

function HousingReportsRoute() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const allowed = hasEffectivePermission(
    user,
    "housing.reports.export_sensitive",
  )
  useEffect(() => {
    if (!user?.account_id || allowed) return
    const queryKey = reportingKeys.root(user.account_id)
    const remove = () => queryClient.removeQueries({ queryKey })
    void queryClient.cancelQueries({ queryKey }).finally(remove)
    remove()
  }, [allowed, queryClient, user?.account_id])
  if (!allowed) return <Navigate to="/" replace />
  return <HousingReportsPage />
}

type EvaluationChildCapability = "game_accounts" | "admin"

function EvaluationCapabilityRoute({
  capability,
  children,
}: {
  capability: EvaluationChildCapability
  children: ReactNode
}) {
  const context = useEvaluationContext()
  const accessError = useEvaluationAccessError(context.error)
  if (context.isPending || accessError.permissionRevoked) {
    return <PortalRouteLoading />
  }
  if (context.isError) {
    return (
      <div className="portalState" role="alert">
        <p>{accessError.message}</p>
        <button type="button" onClick={() => void context.refetch()}>
          重试
        </button>
      </div>
    )
  }
  const allowed =
    capability === "game_accounts"
      ? context.data?.game_accounts_available === true
      : context.data?.admin_available === true &&
        Object.values(context.data.admin_sections).some(Boolean)
  if (!allowed) return <Navigate to="/evaluations" replace />
  return (
    <EvaluationAuthorizationProvider
      pending={context.isFetching}
      revision={context.dataUpdatedAt}
    >
      <div hidden={context.isFetching} aria-hidden={context.isFetching || undefined}>
        {children}
      </div>
      {context.isFetching ? <PortalRouteLoading /> : null}
    </EvaluationAuthorizationProvider>
  )
}

function PortalCapabilityRoute({
  capability,
  children,
}: {
  capability: PortalCapability
  children: ReactNode
}) {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const context = usePortalContext()
  const accessError = usePortalAccessError(context.error)
  useEffect(() => {
    if (
      !user?.account_id ||
      !context.isSuccess ||
      context.isFetching
    ) {
      return
    }
    const purge = (queryKey: readonly unknown[], exact = false) => {
      const remove = () => queryClient.removeQueries({ queryKey, exact })
      void queryClient.cancelQueries({ queryKey, exact }).finally(remove)
      remove()
    }
    if (!context.data.housing_available) {
      purge(portalKeys.housing(user.account_id), true)
    }
    if (!context.data.housing_management_available) {
      purge(housingKeys.root(user.account_id))
    }
  }, [
    context.data?.housing_available,
    context.data?.housing_management_available,
    context.isFetching,
    context.isSuccess,
    queryClient,
    user?.account_id,
  ])
  if (context.isPending || context.isFetching || accessError.permissionRevoked) {
    return <PortalRouteLoading />
  }
  if (context.isError) {
    return (
      <div className="portalState" role="alert">
        <p>{accessError.message}</p>
        <button type="button" onClick={() => void context.refetch()}>
          重试
        </button>
      </div>
    )
  }
  if (!context.data?.[capability]) return <Navigate to="/" replace />
  return children
}

function PermissionAdministrationRoute() {
  const { user, refreshUser } = useAuth()
  const allowed =
    user?.permissions.includes("permissions.manage") ||
    user?.scoped_permissions?.includes("permissions.manage")
  if (!allowed) return <Navigate to="/" replace />
  return (
    <PermissionsPage
      canManageGlobalPermissions={
        user?.permissions.includes("permissions.manage") === true
      }
      currentAccountId={user?.account_id}
      onCurrentAccountAccessChanged={refreshUser}
    />
  )
}

function MailSettingsRoute() {
  const { user } = useAuth()
  if (!user?.permissions.includes("permissions.manage")) {
    return <Navigate to="/" replace />
  }
  return <MailSettingsPage />
}

function TeachingCatalogRoute() {
  const { user } = useAuth()
  const permissions = new Set(user?.permissions ?? [])
  if (!permissions.has("teaching.read")) return <Navigate to="/" replace />
  return (
    <TeachingCatalogPage
      permissions={permissions}
      scopedPermissions={new Set(user?.scoped_permissions ?? [])}
    />
  )
}

function hasCohortAccess(
  permissions: readonly string[] | undefined,
  scopedPermissions: readonly string[] | undefined,
): boolean {
  return (
    permissions?.includes("teaching.read") === true ||
    scopedPermissions?.includes("teaching.read") === true
  )
}

function CohortsRoute() {
  const { user } = useAuth()
  if (!hasCohortAccess(user?.permissions, user?.scoped_permissions)) {
    return <Navigate to="/" replace />
  }
  return (
    <CohortsPage
      permissions={new Set(user?.permissions ?? [])}
      scopedPermissions={new Set(user?.scoped_permissions ?? [])}
    />
  )
}

function CohortDetailRoute() {
  const { user } = useAuth()
  const { cohortId } = useParams()
  if (!cohortId || !hasCohortAccess(user?.permissions, user?.scoped_permissions)) {
    return <Navigate to="/" replace />
  }
  return (
    <CohortDetailPage
      cohortId={cohortId}
      permissions={new Set(user?.permissions ?? [])}
      scopedPermissions={new Set(user?.scoped_permissions ?? [])}
    />
  )
}

function hasReadAccess(
  permissions: readonly string[] | undefined,
  scopedPermissions: readonly string[] | undefined,
  permission: string,
): boolean {
  return (
    permissions?.includes(permission) === true ||
    scopedPermissions?.includes(permission) === true
  )
}

function HousingRoute() {
  const { user } = useAuth()
  const navigate = useNavigate()
  return (
    <HousingOverviewPage
      permissions={new Set(user?.permissions ?? [])}
      scopedPermissions={new Set(user?.scoped_permissions ?? [])}
      onViewRoom={(roomId) => navigate(`/housing/rooms/${roomId}`)}
    />
  )
}

function PaymentsRoute() {
  const { user } = useAuth()
  const permissions = new Set(user?.permissions ?? [])
  if (!permissions.has("payments.read")) return <Navigate to="/" replace />
  return (
    <PaymentsPage
      permissions={permissions}
      scopedPermissions={new Set(user?.scoped_permissions ?? [])}
    />
  )
}

function AdmissionCasesRoute() {
  const { user } = useAuth()
  if (
    !hasReadAccess(
      user?.permissions,
      user?.scoped_permissions,
      "admission_cases.read",
    )
  ) {
    return <Navigate to="/" replace />
  }
  return (
    <AdmissionCasesPage
      permissions={new Set(user?.permissions ?? [])}
      scopedPermissions={new Set(user?.scoped_permissions ?? [])}
    />
  )
}

function EnrollmentsRoute({ detail = false }: { detail?: boolean }) {
  const { user } = useAuth()
  const { enrollmentId } = useParams()
  if (!hasReadAccess(user?.permissions, user?.scoped_permissions, "enrollments.read")) {
    return <Navigate to="/" replace />
  }
  if (!detail) {
    return (
      <EnrollmentsPage
        permissions={new Set(user?.permissions ?? [])}
        scopedPermissions={new Set(user?.scoped_permissions ?? [])}
      />
    )
  }
  if (!enrollmentId) return <Navigate to="/" replace />
  return (
    <EnrollmentDetailPage
      enrollmentId={enrollmentId}
      permissions={new Set(user?.permissions ?? [])}
      scopedPermissions={new Set(user?.scoped_permissions ?? [])}
    />
  )
}

export const appRoutes: RouteObject[] = [
  { path: "/login", element: <LoginBoundary /> },
  { path: "/register", element: <RegistrationPage /> },
  { path: "/change-password", element: <PasswordBoundary /> },
  {
    path: "/evaluation-connect/:requestId",
    element: <EvaluationConnectBoundary />,
  },
  {
    element: <AuthenticatedLayout />,
    children: [
      { index: true, element: <PortalDashboardPage /> },
      {
        path: "/evaluations",
        element: (
          <PermissionRoute permission="evaluation_center.access">
            <EvaluationCenterPage />
          </PermissionRoute>
        ),
      },
      {
        path: "/evaluations/system",
        element: (
          <PermissionRoute permission="evaluation_center.access">
            <SystemEvaluationPage />
          </PermissionRoute>
        ),
      },
      {
        path: "/evaluations/game-accounts",
        element: (
          <PermissionRoute permission="evaluation_center.access">
            <EvaluationCapabilityRoute capability="game_accounts">
              <GameAccountsPage />
            </EvaluationCapabilityRoute>
          </PermissionRoute>
        ),
      },
      {
        path: "/evaluations/admin",
        element: (
          <PermissionRoute permission="evaluation_center.access">
            <EvaluationCapabilityRoute capability="admin">
              <EvaluationAdminPage />
            </EvaluationCapabilityRoute>
          </PermissionRoute>
        ),
      },
      {
        path: "/coach/next-cohorts",
        element: (
          <PortalCapabilityRoute capability="coach_available">
            <CoachNextCohortsPage />
          </PortalCapabilityRoute>
        ),
      },
      {
        path: "/learning",
        element: (
          <PortalCapabilityRoute capability="learning_available">
            <LearningPage />
          </PortalCapabilityRoute>
        ),
      },
      {
        path: "/learning/:studentId",
        element: (
          <PortalCapabilityRoute capability="learning_available">
            <LearningPage />
          </PortalCapabilityRoute>
        ),
      },
      {
        path: "/me/payments",
        element: (
          <PortalCapabilityRoute capability="payments_available">
            <MyPaymentsPage />
          </PortalCapabilityRoute>
        ),
      },
      {
        path: "/me/housing",
        element: (
          <PortalCapabilityRoute capability="housing_available">
            <MyHousingPage />
          </PortalCapabilityRoute>
        ),
      },
      { path: "/people", element: <PeopleDirectoryRoute /> },
      { path: "/people/new", element: <NewPersonRoute /> },
      { path: "/people/:personId/edit", element: <EditPersonRoute /> },
      { path: "/people/:personId", element: <PersonDetailRoute /> },
      {
        path: "/housing",
        element: (
          <PortalCapabilityRoute capability="housing_management_available">
            <HousingRoute />
          </PortalCapabilityRoute>
        ),
      },
      {
        path: "/housing/rooms/:roomId",
        element: (
          <PortalCapabilityRoute capability="housing_management_available">
            <HousingRoomDetailPage />
          </PortalCapabilityRoute>
        ),
      },
      {
        path: "/housing/reports",
        element: <HousingReportsRoute />,
      },
      { path: "/teaching/catalog", element: <TeachingCatalogRoute /> },
      { path: "/teaching/cohorts", element: <CohortsRoute /> },
      { path: "/teaching/cohorts/:cohortId", element: <CohortDetailRoute /> },
      { path: "/teaching/payments", element: <PaymentsRoute /> },
      { path: "/teaching/admission-cases", element: <AdmissionCasesRoute /> },
      { path: "/teaching/enrollments", element: <EnrollmentsRoute /> },
      {
        path: "/teaching/enrollments/:enrollmentId",
        element: <EnrollmentsRoute detail />,
      },
      {
        path: "/settings/accounts",
        element: (
          <PermissionRoute permission="accounts.manage">
            <AccountsPage />
          </PermissionRoute>
        ),
      },
      {
        path: "/settings/permissions",
        element: <PermissionAdministrationRoute />,
      },
      { path: "/settings/mail", element: <MailSettingsRoute /> },
      {
        path: "/settings/audit",
        element: (
          <PermissionRoute permission="audit.read">
            <AuditPage />
          </PermissionRoute>
        ),
      },
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
]

const browserRouter = createBrowserRouter(appRoutes)

export function AppRouter() {
  return <RouterProvider router={browserRouter} />
}
