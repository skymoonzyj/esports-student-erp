import { useQuery, useQueryClient } from "@tanstack/react-query"
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react"
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
import { AssetScanPage } from "../assets/AssetScanPage"
import { AssetsPage } from "../assets/AssetsPage"
import { MyAssetsPage } from "../assets/MyAssetsPage"
import { EnrollmentDetailPage } from "../admissions/EnrollmentDetailPage"
import { EnrollmentsPage } from "../admissions/EnrollmentsPage"
import { PaymentsPage } from "../admissions/PaymentsPage"
import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import { ChangePasswordPage, LoginPage } from "../auth/LoginPage"
import { ForgotPasswordPage } from "../registration/ForgotPasswordPage"
import { AdultGuardianInvitationsPage } from "../registration/AdultGuardianInvitationsPage"
import { GuardianInvitationPage } from "../registration/GuardianInvitationPage"
import { RegistrationAdminPage } from "../registration/RegistrationAdminPage"
import { RegistrationGuardianPage } from "../registration/RegistrationGuardianPage"
import { RegistrationProfilePage } from "../registration/RegistrationProfilePage"
import { RegistrationStartPage } from "../registration/RegistrationStartPage"
import { RegistrationStatusPage } from "../registration/RegistrationStatusPage"
import { RegistrationVerifyPage } from "../registration/RegistrationVerifyPage"
import { StaffApplicationsAdminPage } from "../registration/StaffApplicationsAdminPage"
import { AppShell } from "../layout/AppShell"
import { EvaluationCenterPage } from "../evaluations/EvaluationCenterPage"
import { EvaluationConnectPage } from "../evaluations/EvaluationConnectPage"
import { EvaluationAdminPage } from "../evaluations/EvaluationAdminPage"
import { EvaluationAuthorizationProvider } from "../evaluations/EvaluationAuthorization"
import { EvaluationTaskDetail } from "../evaluations/EvaluationTaskDetail"
import { EvaluationTaskList } from "../evaluations/EvaluationTaskList"
import { GameAccountsPage } from "../evaluations/GameAccountsPage"
import { CampSurveysPage } from "../evaluations/CampSurveysPage"
import { MySurveyFeedbackPage } from "../evaluations/MySurveyFeedbackPage"
import { CampSurveyAdminPage } from "../evaluations/CampSurveyAdminPage"
import { CampSurveyPrintPage } from "../evaluations/CampSurveyPrintPage"
import { reportKeys } from "../evaluations/reportKeys"
import { SystemEvaluationPage } from "../evaluations/SystemEvaluationPage"
import { TeachingReportReviewPage } from "../evaluations/TeachingReportReviewPage"
import { TeachingReportsPage } from "../evaluations/TeachingReportsPage"
import { systemEvaluationKeys } from "../evaluations/systemEvaluationKeys"
import { useEvaluationAccessError } from "../evaluations/useEvaluationAccessError"
import { useEvaluationContext } from "../evaluations/useEvaluationContext"
import { useTeachingReportContext } from "../evaluations/useTeachingReportContext"
import { EnrollmentCatalogPage } from "../enrollment/EnrollmentCatalogPage"
import {
  foodOnboardingPath,
  FoodOnboardingPage,
} from "../foodPreferences/FoodOnboardingPage"
import { FoodCuisineSettingsPage } from "../foodPreferences/FoodCuisineSettingsPage"
import { FoodPreferenceStatisticsPage } from "../foodPreferences/FoodPreferenceStatisticsPage"
import { getFoodPreferenceCatalog } from "../foodPreferences/api"
import { foodPreferenceKeys } from "../foodPreferences/keys"
import { MyFoodProfilePage } from "../foodPreferences/MyFoodProfilePage"
import { HousingOverviewPage } from "../housing/HousingOverviewPage"
import { HousingReportsPage } from "../housing/HousingReportsPage"
import { HousingRoomDetailPage } from "../housing/HousingRoomDetailPage"
import { MyHousingPage } from "../housing/MyHousingPage"
import { housingKeys } from "../housing/housingKeys"
import { reportingKeys } from "../housing/reportingKeys"
import { PeoplePage } from "../people/PeoplePage"
import { PersonDetailPage } from "../people/PersonDetailPage"
import { PersonForm } from "../people/PersonForm"
import { SelfProfilePanel } from "../people/SelfProfilePanel"
import { OperationsMonitorPage } from "../operations/OperationsMonitorPage"
import { CoachNextCohortsPage } from "../portal/CoachNextCohortsPage"
import { LearningPage } from "../portal/LearningPage"
import { MyPaymentsPage } from "../portal/MyPaymentsPage"
import type { PortalCapability } from "../portal/api"
import { portalKeys } from "../portal/api"
import { PortalDashboardPage } from "../portal/PortalDashboardPage"
import { usePortalAccessError } from "../portal/usePortalAccessError"
import { usePortalContext } from "../portal/usePortalContext"
import { ProcurementPage } from "../procurement/ProcurementPage"
import { MyRevenueSharePage } from "../revenueShare/MyRevenueSharePage"
import { RevenueShareFinancePage } from "../revenueShare/RevenueShareFinancePage"
import { RevenueShareManagementPage } from "../revenueShare/RevenueShareManagementPage"
import { AccountsPage } from "../settings/AccountsPage"
import { AuditPage } from "../settings/AuditPage"
import { MailSettingsPage } from "../settings/MailSettingsPage"
import { PermissionsPage } from "../settings/PermissionsPage"
import { CohortDetailPage } from "../teaching/CohortDetailPage"
import { CohortsPage } from "../teaching/CohortsPage"
import { TeachingCatalogPage } from "../teaching/TeachingCatalogPage"
import { getTeamContext } from "../teams/api"
import { teamKeys } from "../teams/keys"
import { TeamManagementPage } from "../teams/TeamManagementPage"
import { GameHost } from "../talentTests/GameHost"
import { TalentHistoryPage } from "../talentTests/TalentHistoryPage"
import { TalentReportPage } from "../talentTests/TalentReportPage"
import { TalentTestsPage } from "../talentTests/TalentTestsPage"
import { getTalentContext } from "../talentTests/api"
import { TEST_IDS, type TestId } from "../talentTests/contracts"
import { TravelReportsPage } from "../travelReports/TravelReportsPage"
import { AssessmentLayout } from "../esportsAssessments/AssessmentLayout"

const AssessmentHome = lazy(() => import("../esportsAssessments/AssessmentHome"))
const AssessmentSessionPage = lazy(() => import("../esportsAssessments/AssessmentSessionPage"))
const AssessmentPracticePage = lazy(() => import("../esportsAssessments/PracticePage"))
const AssessmentHistoryPage = lazy(() => import("../esportsAssessments/AssessmentHistoryPage"))
const AssessmentReportPage = lazy(() => import("../esportsAssessments/AssessmentReportPage"))

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
const UUID_V4_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const GUARDIAN_INVITATION_PATH = "/guardian-invitation"

function safeLoginReturn(state: unknown): string {
  if (!state || typeof state !== "object") return "/"
  const returnTo = (state as LoginRouteState).returnTo
  return typeof returnTo === "string" &&
    (returnTo === GUARDIAN_INVITATION_PATH || EVALUATION_CONNECT_PATH.test(returnTo))
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
  const queryClient = useQueryClient()
  const portalContext = usePortalContext()
  const teamContext = useQuery({
    queryKey: teamKeys(user?.account_id ?? "anonymous").context(),
    queryFn: getTeamContext,
    enabled: status === "authenticated" && Boolean(user?.account_id),
    refetchOnWindowFocus: "always",
    staleTime: 1000,
    retry: false,
  })
  useEffect(() => {
    if (!user?.account_id || !teamContext.data || teamContext.isFetching) return
    const keys = teamKeys(user.account_id)
    const revokeManagement = !teamContext.data.can_open_management
    const revokeSelf = !teamContext.data.can_read_self
    if (!revokeManagement && !revokeSelf) return
    const queries = queryClient.getQueryCache().findAll({ queryKey: keys.root })
    for (const query of queries) {
      const segment = query.queryKey[2]
      if (segment === "context") continue
      if ((segment === "self" && revokeSelf) || (segment !== "self" && revokeManagement)) {
        void queryClient.cancelQueries({ queryKey: query.queryKey, exact: true })
        queryClient.removeQueries({ queryKey: query.queryKey, exact: true })
      }
    }
  }, [queryClient, teamContext.data, teamContext.isFetching, user?.account_id])
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
          "assets.read_self",
          "assets.read",
          "procurement.read",
          "travel_reports.read",
          "revenue_share.read_self",
          "enrollment_applications.create",
          "talent_tests.take",
          "talent_tests.read",
          "esports_assessments.take",
          "esports_assessments.read",
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
      isSuperAdmin={user?.is_super_admin === true}
      portalContext={
        // Keep the rail stable while a route guard revalidates capabilities.
        // A failed/revoked response still removes the stale entries before
        // the guard redirects, so this does not grant access to private data.
        portalContext.data && !portalContext.error
          ? portalContext.data
          : undefined
      }
      teamContext={teamContext.data && !teamContext.error && !teamContext.isFetching
        ? teamContext.data : undefined}
      userName={user?.full_name}
      onLogout={() => void logout()}
    >
      <Outlet />
    </AppShell>
  )
}

function TeamManagementRoute() {
  const { user } = useAuth()
  const context = useQuery({
    queryKey: teamKeys(user?.account_id ?? "anonymous").context(),
    queryFn: getTeamContext,
    enabled: Boolean(user?.account_id),
    refetchOnWindowFocus: "always",
    staleTime: 1000,
    retry: false,
  })
  if (!user) return <Navigate to="/" replace />
  if (context.isPending) return <p>正在确认队伍权限…</p>
  if (context.isError) return <p role="alert">暂时无法确认队伍权限。</p>
  if (!context.data?.can_open_management) return <Navigate to="/" replace />
  return <TeamManagementPage accountId={user.account_id} />
}

function TeamProfileRoute() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const portalContext = usePortalContext()
  const context = useQuery({
    queryKey: teamKeys(user?.account_id ?? "anonymous").context(),
    queryFn: getTeamContext,
    enabled: Boolean(user?.account_id),
    refetchOnWindowFocus: "always",
    staleTime: 1000,
    retry: false,
  })
  const canReadTeam = context.data?.can_read_self === true && !context.isError
  const portalRevoked = portalContext.error instanceof ProblemDetailsError &&
    (portalContext.error.problem.status === 403 || portalContext.error.problem.status === 404)
  useEffect(() => {
    if (!user?.account_id || portalContext.isPending || portalContext.isFetching ||
      (portalContext.isSuccess && portalContext.data.food_profile_available)) return
    const queryKey = foodPreferenceKeys.accountRoot(user.account_id)
    const remove = () => queryClient.removeQueries({ queryKey })
    void queryClient.cancelQueries({ queryKey }).finally(remove)
    remove()
  }, [portalContext.data?.food_profile_available, portalContext.isFetching, portalContext.isPending, portalContext.isSuccess, queryClient, user?.account_id])
  useEffect(() => {
    if (!user?.account_id || !portalRevoked || context.isPending || context.isFetching || canReadTeam) return
    const queryKey = portalKeys.root(user.account_id)
    const remove = () => queryClient.removeQueries({ queryKey })
    void queryClient.cancelQueries({ queryKey }).finally(remove)
    remove()
    navigate("/", { replace: true })
  }, [canReadTeam, context.isFetching, context.isPending, navigate, portalRevoked, queryClient, user?.account_id])
  if (!user) return <Navigate to="/" replace />
  if (portalContext.isSuccess && !portalContext.isFetching && portalContext.data.food_profile_available) {
    return <MyFoodProfilePage />
  }
  if (canReadTeam) {
    return <div className="portalPage"><h1>个人资料</h1><SelfProfilePanel personId={user.person_id} accountId={user.account_id} /></div>
  }
  if (portalContext.isPending || portalContext.isFetching || context.isPending || context.isFetching || portalRevoked) return <PortalRouteLoading />
  if (portalContext.isError) return <div className="portalState" role="alert"><p>{portalContext.error instanceof ProblemDetailsError ? portalContext.error.problem.detail : "服务暂时不可用，请重试。"}</p><button type="button" onClick={() => void portalContext.refetch()}>重试</button></div>
  return <Navigate to="/" replace />
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
  return (
    <PersonDetailPage
      permissions={new Set(user?.permissions ?? [])}
      scopedPermissions={new Set(user?.scoped_permissions ?? [])}
    />
  )
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

export function AnyPermissionRoute({
  permissions,
  children,
}: {
  permissions: readonly string[]
  children: ReactNode
}) {
  const { user } = useAuth()
  if (!permissions.some((permission) => hasEffectivePermission(user, permission))) {
    return <Navigate to="/" replace />
  }
  return children
}

function TalentLandingRoute() {
  const { user } = useAuth()
  if (!user) return <Navigate to="/" replace />
  return <TalentTestsPage accountId={user.account_id} personId={user.person_id} />
}

function TalentHistoryRoute() {
  const { user } = useAuth()
  const context = useQuery({
    queryKey: ["talent-tests", user?.account_id ?? "anonymous", "context"],
    queryFn: getTalentContext,
    enabled: Boolean(user),
  })
  if (!user) return <Navigate to="/" replace />
  if (context.isPending) return <p>正在确认天赋测试权限…</p>
  if (context.isError || !context.data) {
    return <p role="alert">无法确认天赋测试权限，请稍后重试。</p>
  }
  if (!context.data.can_read_self) return <Navigate to="/talent-tests" replace />
  return <TalentHistoryPage accountId={user.account_id} />
}

function TalentReportRoute() {
  const { user } = useAuth()
  const { reportId } = useParams()
  if (!user || !reportId) return <Navigate to="/talent-tests" replace />
  return <TalentReportPage accountId={user.account_id} reportId={reportId} />
}

function TalentGameRoute() {
  const { user } = useAuth()
  const { sessionId, testId } = useParams()
  const navigate = useNavigate()
  const hasKeyAccess = Boolean(
    user
    && hasEffectivePermission(user, "talent_tests.take")
    && hasEffectivePermission(user, "talent_tests.read"),
  )
  const context = useQuery({
    queryKey: ["talent-tests", user?.account_id ?? "anonymous", "context"],
    queryFn: getTalentContext,
    enabled: hasKeyAccess,
  })
  if (!hasKeyAccess || !user) {
    return <Navigate to="/" replace />
  }
  if (context.isPending) return <p>正在确认天赋测试权限…</p>
  if (context.isError || !context.data) {
    return <p role="alert">无法确认天赋测试权限，请稍后重试。</p>
  }
  if (!context.data.can_take || !context.data.can_read_self) {
    return <Navigate to="/talent-tests" replace />
  }
  if (!sessionId || !testId || !TEST_IDS.includes(testId as TestId)) {
    return <p role="alert">该测试项目不可用。</p>
  }
  return (
    <GameHost
      accountId={user.account_id}
      personId={user.person_id}
      sessionId={sessionId}
      testId={testId as TestId}
      onSessionCompleted={(reportId) => {
        if (!UUID_V4_PATTERN.test(reportId)) return
        navigate(`/talent-tests/reports/${reportId}`, { replace: true })
      }}
    />
  )
}

function SuperAdminRoute({
  permission,
  children,
}: {
  permission: string
  children: ReactNode
}) {
  const { user } = useAuth()
  if (user?.is_super_admin !== true || !user.permissions.includes(permission)) {
    return <Navigate to="/" replace />
  }
  return children
}

function HousingManagementRoute({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const allowed = hasEffectivePermission(user, "housing.read")
  useEffect(() => {
    if (!user?.account_id || allowed) return
    const queryKey = housingKeys.root(user.account_id)
    const remove = () => queryClient.removeQueries({ queryKey })
    void queryClient.cancelQueries({ queryKey }).finally(remove)
    remove()
  }, [allowed, queryClient, user?.account_id])
  if (!allowed) return <Navigate to="/" replace />
  return (
    <PortalCapabilityRoute capability="housing_management_available">
      {children}
    </PortalCapabilityRoute>
  )
}

function FoodAdministrationRoute({
  kind,
}: {
  kind: "statistics" | "cuisines"
}) {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [accessRevoked, setAccessRevoked] = useState(false)
  const permission = kind === "statistics"
    ? "food_preferences.export"
    : "food_preferences.catalog.manage"
  const accountId = user?.account_id ?? ""
  const allowed = user?.permissions.includes(permission) === true
  const catalog = useQuery({
    queryKey: foodPreferenceKeys.catalog(accountId),
    queryFn: ({ signal }) => getFoodPreferenceCatalog(kind === "cuisines", signal),
    enabled: Boolean(accountId) && allowed && !accessRevoked,
    staleTime: 0,
    gcTime: 0,
    retry: false,
  })
  const revokeAccess = useCallback(() => {
    if (!accountId) {
      setAccessRevoked(true)
      navigate("/", { replace: true })
      return
    }
    setAccessRevoked(true)
    const queryKey = foodPreferenceKeys.accountRoot(accountId)
    const cancellation = queryClient.cancelQueries({ queryKey })
    queryClient.removeQueries({ queryKey })
    navigate("/", { replace: true })
    void cancellation.finally(() => queryClient.removeQueries({ queryKey }))
  }, [accountId, navigate, queryClient])
  useEffect(() => {
    if (allowed || !accountId) return
    const queryKey = foodPreferenceKeys.accountRoot(accountId)
    const remove = () => queryClient.removeQueries({ queryKey })
    void queryClient.cancelQueries({ queryKey }).finally(remove)
    remove()
  }, [accountId, allowed, queryClient])
  useEffect(() => {
    if (
      catalog.error instanceof ProblemDetailsError &&
      (catalog.error.problem.status === 403 || catalog.error.problem.status === 404)
    ) {
      revokeAccess()
    }
  }, [catalog.error, revokeAccess])
  if (!allowed || accessRevoked) return <Navigate to="/" replace />
  if (catalog.isError) {
    if (
      catalog.error instanceof ProblemDetailsError &&
      (catalog.error.problem.status === 403 || catalog.error.problem.status === 404)
    ) {
      return <p className="routeLoading" aria-live="polite">正在清理已撤销的餐饮后台权限…</p>
    }
    return (
      <main className="routeLoading" role="alert">
        <p>暂时无法确认餐饮后台权限。</p>
        <button type="button" onClick={() => void catalog.refetch()}>重试</button>
      </main>
    )
  }
  if (catalog.isPending || catalog.isFetching || !catalog.data) {
    return <p className="routeLoading" aria-live="polite">正在确认餐饮后台权限…</p>
  }
  return kind === "statistics"
    ? <FoodPreferenceStatisticsPage accessRevision={catalog.data.access_revision} onAccessRevoked={revokeAccess} />
    : <FoodCuisineSettingsPage onAccessRevoked={revokeAccess} />
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

type EvaluationChildCapability = "game_accounts" | "admin" | "system_evaluation"

function EvaluationCapabilityRoute({
  capability,
  children,
}: {
  capability: EvaluationChildCapability
  children: ReactNode
}) {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const context = useEvaluationContext()
  const accessError = useEvaluationAccessError(context.error)
  const allowed = capability === "game_accounts"
    ? context.data?.game_accounts_available === true
    : capability === "system_evaluation"
      ? context.data?.system_evaluation_available === true
      : context.data?.admin_available === true &&
        Object.values(context.data.admin_sections).some(Boolean)
  useEffect(() => {
    if (
      capability !== "system_evaluation" ||
      !user?.account_id ||
      !context.isSuccess ||
      context.isFetching ||
      allowed
    ) {
      return
    }
    const queryKey = systemEvaluationKeys.root(user.account_id)
    const remove = () => queryClient.removeQueries({ queryKey })
    void queryClient.cancelQueries({ queryKey }).finally(remove)
    remove()
  }, [
    allowed,
    capability,
    context.isFetching,
    context.isSuccess,
    queryClient,
    user?.account_id,
  ])
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

type TeachingReportRouteCapability = "author" | "reviewer"

function isTeachingReportOperationDenied(error: unknown): boolean {
  return (
    error instanceof ProblemDetailsError &&
    error.problem.status === 403 &&
    error.problem.code === "evaluation_operation_denied"
  )
}

function isEvaluationAccessDenied(error: unknown): boolean {
  return (
    error instanceof ProblemDetailsError &&
    error.problem.status === 403 &&
    error.problem.code === "evaluation_access_denied"
  )
}

function keyStartsWith(
  queryKey: readonly unknown[],
  prefix: readonly unknown[],
): boolean {
  return prefix.every((value, index) => queryKey[index] === value)
}

function TeachingReportCapabilityRoute({
  capability,
  children,
}: {
  capability: TeachingReportRouteCapability
  children: ReactNode
}) {
  const { user } = useAuth()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const context = useTeachingReportContext()
  const accountId = user?.account_id ?? "anonymous"
  const [descendantAccessError, setDescendantAccessError] = useState<{
    accountId: string
    error: unknown
  } | null>(null)
  const accessError = useEvaluationAccessError([
    context.error,
    descendantAccessError?.accountId === accountId
      ? descendantAccessError.error
      : null,
  ])
  const workspacePermission = capability === "author"
    ? "student_reports.write"
    : "student_reports.review"
  const allowed = capability === "author"
    ? context.data?.author_available === true
      || hasEffectivePermission(user, workspacePermission)
    : context.data?.reviewer_available === true
      || hasEffectivePermission(user, workspacePermission)
  const previous = useRef({ accountId, capability, allowed: false })
  if (
    previous.current.accountId !== accountId ||
    previous.current.capability !== capability
  ) {
    previous.current = { accountId, capability, allowed: false }
  }
  const capabilityRevoked = Boolean(
    context.isSuccess &&
    !context.isFetching &&
    !allowed &&
    previous.current.allowed,
  )

  useEffect(() => {
    if (!context.isSuccess || context.isFetching || !allowed) return
    previous.current.allowed = true
  }, [allowed, context.isFetching, context.isSuccess])

  useEffect(() => {
    if (!user?.account_id) return
    const root = reportKeys.root(user.account_id)
    const contextKey = reportKeys.context(user.account_id)
    const seenErrors = new WeakSet<object>()
    const handleReportError = (error: unknown): void => {
      if (
        typeof error !== "object" ||
        error === null ||
        seenErrors.has(error)
      ) {
        return
      }
      if (isEvaluationAccessDenied(error)) {
        seenErrors.add(error)
        setDescendantAccessError({ accountId: user.account_id, error })
        return
      }
      if (!isTeachingReportOperationDenied(error)) return
      seenErrors.add(error)
      void queryClient.invalidateQueries({ queryKey: contextKey })
    }
    const checkReportQuery = (queryKey: readonly unknown[], error: unknown) => {
      if (!keyStartsWith(queryKey, root)) return
      if (
        queryKey.length === contextKey.length &&
        keyStartsWith(queryKey, contextKey)
      ) {
        return
      }
      handleReportError(error)
    }
    const queryCache = queryClient.getQueryCache()
    const mutationCache = queryClient.getMutationCache()
    const unsubscribeQueries = queryCache.subscribe((event) => {
      if (event.type !== "updated") return
      checkReportQuery(event.query.queryKey, event.query.state.error)
    })
    const unsubscribeMutations = mutationCache.subscribe((event) => {
      if (event.type !== "updated") return
      const mutationKey = event.mutation.options.mutationKey
      if (!mutationKey || !keyStartsWith(mutationKey, root)) return
      handleReportError(event.mutation.state.error)
    })
    for (const query of queryCache.findAll({ queryKey: root })) {
      checkReportQuery(query.queryKey, query.state.error)
    }
    return () => {
      unsubscribeQueries()
      unsubscribeMutations()
    }
  }, [queryClient, user?.account_id])

  useEffect(() => {
    if (
      !user?.account_id ||
      (!capabilityRevoked && !accessError.permissionRevoked)
    ) {
      return
    }
    const queryKey = reportKeys.root(user.account_id)
    const remove = () => queryClient.removeQueries({ queryKey })
    void queryClient.cancelQueries({ queryKey }).finally(() => {
      remove()
      if (capabilityRevoked) navigate("/evaluations", { replace: true })
    })
    remove()
  }, [
    accessError.permissionRevoked,
    capabilityRevoked,
    navigate,
    queryClient,
    user?.account_id,
  ])

  if (context.isPending || accessError.permissionRevoked || capabilityRevoked) {
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
  if (!allowed) {
    if (capability === "author" && context.data.reviewer_available) {
      return <Navigate to="/evaluations/reports/review" replace />
    }
    if (capability === "author" && context.data.family_available) {
      return <Navigate to="/learning" replace />
    }
    return <Navigate to="/evaluations" replace />
  }
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
    if (!context.data.food_profile_available) {
      purge(foodPreferenceKeys.accountRoot(user.account_id))
    }
  }, [
    context.data?.food_profile_available,
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
      canManageTalentPermissions={user?.is_super_admin === true && hasEffectivePermission(user, "permissions.manage")}
      currentAccountId={user?.account_id}
      onCurrentAccountAccessChanged={refreshUser}
    />
  )
}

function AccountsRoute() {
  const { user } = useAuth()
  if (!hasEffectivePermission(user, "accounts.read")) return <Navigate to="/" replace />
  return <AccountsPage canManage={user?.permissions.includes("accounts.manage") === true} />
}

function AssetsRoute() {
  const { user } = useAuth()
  if (!hasEffectivePermission(user, "assets.read")) return <Navigate to="/" replace />
  return (
    <AssetsPage
      canManage={hasEffectivePermission(user, "assets.manage")}
      canAssign={hasEffectivePermission(user, "assets.assign")}
      canExport={hasEffectivePermission(user, "assets.export")}
    />
  )
}

function ProcurementRoute() {
  const { user } = useAuth()
  if (!hasEffectivePermission(user, "procurement.read")) {
    return <Navigate to="/" replace />
  }
  return (
    <ProcurementPage
      canManage={hasEffectivePermission(user, "procurement.manage")}
      canSettle={hasEffectivePermission(user, "procurement.settle")}
      canExport={hasEffectivePermission(user, "procurement.export")}
      canManageCenters={hasEffectivePermission(user, "procurement.centers.manage")}
      canManageInvoices={hasEffectivePermission(user, "procurement.invoices.manage")}
      canCreateAssets={hasEffectivePermission(user, "assets.manage")}
    />
  )
}

function TravelReportsRoute() {
  const { user } = useAuth()
  if (!hasEffectivePermission(user, "travel_reports.read")) {
    return <Navigate to="/" replace />
  }
  return (
    <TravelReportsPage
      canImport={hasEffectivePermission(user, "travel_reports.import")}
      canExport={hasEffectivePermission(user, "travel_reports.export")}
    />
  )
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
  { path: "/change-password", element: <PasswordBoundary /> },
  { path: "/register", element: <RegistrationStartPage /> },
  {
    path: "/register/guardian-invitation",
    element: <RegistrationStartPage guardianInvitation />,
  },
  { path: "/register/verify", element: <RegistrationVerifyPage /> },
  { path: "/register/profile", element: <RegistrationProfilePage /> },
  { path: "/register/guardian", element: <RegistrationGuardianPage /> },
  { path: "/register/status", element: <RegistrationStatusPage /> },
  { path: "/forgot-password", element: <ForgotPasswordPage /> },
  { path: GUARDIAN_INVITATION_PATH, element: <GuardianInvitationPage /> },
  { path: "/asset-scan/:assetCode", element: <AssetScanPage /> },
  {
    path: "/evaluation-connect/:requestId",
    element: <EvaluationConnectBoundary />,
  },
  {
    element: <AuthenticatedLayout />,
    children: [
      { index: true, element: <PortalDashboardPage /> },
      {
        path: "/esports-assessments",
        element: (
          <AnyPermissionRoute permissions={["esports_assessments.take", "esports_assessments.read"]}>
            <Suspense fallback={<p>正在加载天赋评测 2…</p>}>
              <AssessmentLayout />
            </Suspense>
          </AnyPermissionRoute>
        ),
        children: [
          { index: true, element: <AssessmentHome /> },
          { path: 'sessions/:sessionId', element: <AssessmentSessionPage /> },
          { path: 'practice', element: <AssessmentPracticePage /> },
          { path: 'history', element: <AssessmentHistoryPage /> },
          { path: 'reports/:reportId', element: <AssessmentReportPage /> },
        ],
      },
      {
        path: "/talent-tests",
        element: (
          <AnyPermissionRoute permissions={["talent_tests.take", "talent_tests.read"]}>
            <TalentLandingRoute />
          </AnyPermissionRoute>
        ),
      },
      {
        path: "/talent-tests/history",
        element: (
          <PermissionRoute permission="talent_tests.read">
            <TalentHistoryRoute />
          </PermissionRoute>
        ),
      },
      {
        path: "/talent-tests/sessions/:sessionId/:testId",
        element: <TalentGameRoute />,
      },
      {
        path: "/talent-tests/reports/:reportId",
        element: (
          <PermissionRoute permission="talent_tests.read">
            <TalentReportRoute />
          </PermissionRoute>
        ),
      },
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
        path: "/evaluations/reports",
        element: (
          <PermissionRoute permission="evaluation_center.access">
            <TeachingReportCapabilityRoute capability="author">
              <TeachingReportsPage />
            </TeachingReportCapabilityRoute>
          </PermissionRoute>
        ),
      },
      {
        path: "/evaluations/reports/review",
        element: (
          <PermissionRoute permission="evaluation_center.access">
            <TeachingReportCapabilityRoute capability="reviewer">
              <TeachingReportReviewPage />
            </TeachingReportCapabilityRoute>
          </PermissionRoute>
        ),
      },
      {
        path: "/evaluations/system/tasks",
        element: (
          <PermissionRoute permission="evaluation_center.access">
            <EvaluationCapabilityRoute capability="system_evaluation">
              <EvaluationTaskList />
            </EvaluationCapabilityRoute>
          </PermissionRoute>
        ),
      },
      {
        path: "/evaluations/system/tasks/:taskId",
        element: (
          <PermissionRoute permission="evaluation_center.access">
            <EvaluationCapabilityRoute capability="system_evaluation">
              <EvaluationTaskDetail />
            </EvaluationCapabilityRoute>
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
        path: "/evaluations/surveys",
        element: (
          <PermissionRoute permission="evaluation_center.access">
            <CampSurveysPage />
          </PermissionRoute>
        ),
      },
      {
        path: "/evaluations/surveys/feedback",
        element: (
          <PermissionRoute permission="evaluation_center.access">
            <MySurveyFeedbackPage />
          </PermissionRoute>
        ),
      },
      {
        path: "/evaluations/surveys/admin",
        element: (
          <PermissionRoute permission="evaluation_center.access">
            <CampSurveyAdminPage />
          </PermissionRoute>
        ),
      },
      {
        path: "/evaluations/surveys/print",
        element: (
          <PermissionRoute permission="evaluation_center.access">
            <CampSurveyPrintPage />
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
        path: "/enrollment",
        element: (
          <PermissionRoute permission="enrollment_applications.create">
            <EnrollmentCatalogPage />
          </PermissionRoute>
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
        path: "/me/profile",
        element: <TeamProfileRoute />,
      },
      {
        path: "/me/guardian-invitations",
        element: (
          <PortalCapabilityRoute capability="adult_guardian_invitations_available">
            <AdultGuardianInvitationsPage />
          </PortalCapabilityRoute>
        ),
      },
      {
        path: foodOnboardingPath,
        element: (
          <PortalCapabilityRoute capability="food_preferences_onboarding_required">
            <FoodOnboardingPage />
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
      {
        path: "/me/assets",
        element: (
          <PermissionRoute permission="assets.read_self">
            <MyAssetsPage />
          </PermissionRoute>
        ),
      },
      {
        path: "/me/revenue-share",
        element: (
          <PermissionRoute permission="revenue_share.read_self">
            <MyRevenueSharePage />
          </PermissionRoute>
        ),
      },
      { path: "/people", element: <PeopleDirectoryRoute /> },
      { path: "/people/teams", element: <TeamManagementRoute /> },
      { path: "/people/new", element: <NewPersonRoute /> },
      { path: "/people/:personId/edit", element: <EditPersonRoute /> },
      { path: "/people/:personId", element: <PersonDetailRoute /> },
      {
        path: "/housing",
        element: (
          <HousingManagementRoute>
            <HousingRoute />
          </HousingManagementRoute>
        ),
      },
      {
        path: "/housing/rooms/:roomId",
        element: (
          <HousingManagementRoute>
            <HousingRoomDetailPage />
          </HousingManagementRoute>
        ),
      },
      {
        path: "/housing/reports",
        element: <HousingReportsRoute />,
      },
      { path: "/assets", element: <AssetsRoute /> },
      { path: "/procurement", element: <ProcurementRoute /> },
      { path: "/travel-reports", element: <TravelReportsRoute /> },
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
        path: "/finance/revenue-share",
        element: (
          <PermissionRoute permission="revenue_share.finance">
            <RevenueShareFinancePage />
          </PermissionRoute>
        ),
      },
      {
        path: "/settings/revenue-share",
        element: (
          <PermissionRoute permission="revenue_share.manage">
            <RevenueShareManagementPage />
          </PermissionRoute>
        ),
      },
      {
        path: "/settings/accounts",
        element: <AccountsRoute />,
      },
      {
        path: "/settings/registrations",
        element: (
          <PermissionRoute permission="registration.review">
            <RegistrationAdminPage />
          </PermissionRoute>
        ),
      },
      {
        path: "/settings/staff-applications",
        element: (
          <PermissionRoute permission="staff_identity_applications.review">
            <StaffApplicationsAdminPage />
          </PermissionRoute>
        ),
      },
      {
        path: "/settings/permissions",
        element: <PermissionAdministrationRoute />,
      },
      {
        path: "/settings/mail",
        element: (
          <PermissionRoute permission="mail.settings.manage">
            <MailSettingsPage />
          </PermissionRoute>
        ),
      },
      {
        path: "/settings/operations",
        element: (
          <SuperAdminRoute permission="operations.monitor.read">
            <OperationsMonitorPage />
          </SuperAdminRoute>
        ),
      },
      {
        path: "/settings/audit",
        element: (
          <SuperAdminRoute permission="audit.read">
            <AuditPage />
          </SuperAdminRoute>
        ),
      },
      {
        path: "/people/food-preferences",
        element: <FoodAdministrationRoute kind="statistics" />,
      },
      {
        path: "/settings/food-cuisines",
        element: <FoodAdministrationRoute kind="cuisines" />,
      },
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
]

const browserRouter = createBrowserRouter(appRoutes)

export function AppRouter() {
  return <RouterProvider router={browserRouter} />
}
