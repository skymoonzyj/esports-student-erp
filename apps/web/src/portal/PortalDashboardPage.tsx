import { useQuery } from "@tanstack/react-query"
import {
  ArrowRight,
  BedDouble,
  BookOpenCheck,
  ClipboardPenLine,
  ClipboardList,
  MessageSquareText,
  RefreshCw,
  WalletCards,
} from "lucide-react"
import { Link } from "react-router-dom"

import { useAuth } from "../auth/AuthProvider"
import { DashboardPage } from "../layout/AppShell"
import { HousingPropertyChangesPanel } from "../housing/HousingPropertyChangesPanel"
import { DepartureRemindersPanel } from "./DepartureRemindersPanel"
import { useDepartureReminders } from "./useDepartureReminders"
import { RentalRemindersPanel } from "./RentalRemindersPanel"
import { useRentalReminders } from "./useRentalReminders"
import { RoomCardRemindersContent } from "./RoomCardRemindersPanel"
import { useRoomCardReminders } from "./useRoomCardReminders"
import {
  getCoachNextCohorts,
  getLearningStudents,
  portalKeys,
} from "./api"
import { usePortalAccessError } from "./usePortalAccessError"
import {
  guardedPortalQueryOptions,
  usePortalContext,
} from "./usePortalContext"

import "./portal.css"

const BACK_OFFICE_PERMISSIONS = new Set([
  "people.list",
  "accounts.manage",
  "teaching.read",
  "admission_cases.read",
  "housing.read",
  "audit.read",
  "accounts.read",
  "assets.read",
  "payments.read",
  "procurement.read",
  "travel_reports.read",
  "revenue_share.finance",
  "enrollments.read",
  "food_preferences.export",
])

function WorkspacePropertyReview({ enabled, ready }: { enabled: boolean; ready: boolean }) {
  if (!enabled) return null
  return <div hidden={!ready} inert={!ready}><HousingPropertyChangesPanel mode="review" enabled={enabled} /></div>
}

export function PortalDashboardPage() {
  const { user } = useAuth()
  const accountId = user?.account_id ?? "anonymous"
  const contextQuery = usePortalContext()
  const context = contextQuery.data
  const contextReady = Boolean(context && contextQuery.isSuccess && !contextQuery.isFetching)
  const propertyReviewEnabled = contextQuery.isSuccess && context?.housing_management_available === true && Boolean(user && (
    user.is_super_admin || user.permissions.includes("housing.read") || user.scoped_permissions?.includes("housing.read")
  ))
  const coachEnabled = contextReady && context?.coach_available === true
  const learningEnabled = contextReady && context?.learning_available === true
  const departures = useDepartureReminders({
    accountId: user?.account_id ?? null,
    contextReady,
    contextEpoch: contextQuery.dataUpdatedAt,
  })
  usePortalAccessError(departures.query.error)
  const rentals = useRentalReminders({
    accountId: user?.account_id ?? null,
    contextReady,
    contextEpoch: contextQuery.dataUpdatedAt,
  })
  usePortalAccessError(rentals.query.error)
  const roomCards = useRoomCardReminders({
    accountId: user?.account_id ?? null,
    contextReady,
    contextEpoch: contextQuery.dataUpdatedAt,
  })
  usePortalAccessError(roomCards.query.error)

  const coachQuery = useQuery({
    queryKey: portalKeys.coach(accountId, undefined, 1),
    queryFn: () => getCoachNextCohorts({ page: 1, pageSize: 1 }),
    enabled: coachEnabled,
    ...guardedPortalQueryOptions,
  })
  const studentsQuery = useQuery({
    queryKey: portalKeys.students(accountId),
    queryFn: getLearningStudents,
    enabled: learningEnabled,
    ...guardedPortalQueryOptions,
  })

  const portalError = contextQuery.error ?? coachQuery.error ?? studentsQuery.error
  const accessError = usePortalAccessError(portalError)
  const hasPortalNavigation = Boolean(
    context?.coach_available ||
      context?.learning_available ||
      context?.payments_available ||
      context?.housing_available ||
      context?.invitations_available,
  )
  const hasPortalCapability = hasPortalNavigation || departures.query.data?.available === true || rentals.query.data?.available === true || roomCards.query.data?.available === true
  const hasBackOfficeCapability = Boolean(
    user?.permissions.some((permission) => BACK_OFFICE_PERMISSIONS.has(permission)) ||
      user?.scoped_permissions?.some((permission) =>
        BACK_OFFICE_PERMISSIONS.has(permission),
      ),
  )
  const evaluationAvailable =
    user?.permissions.includes("evaluation_center.access") === true
  const enrollmentAvailable = Boolean(
    user?.permissions.includes("enrollment_applications.create") ||
      user?.scoped_permissions?.includes("enrollment_applications.create"),
  )
  const privateDataFetching =
    contextQuery.isFetching ||
    (coachEnabled && coachQuery.isFetching) ||
    (learningEnabled && studentsQuery.isFetching)

  const noPortalTasks =
    !privateDataFetching &&
    !portalError &&
    !hasPortalNavigation &&
    !evaluationAvailable &&
    !enrollmentAvailable
  const showEmpty = noPortalTasks && !hasBackOfficeCapability && !hasPortalCapability &&
    !departures.query.isPending && !departures.query.isError && !rentals.query.isPending && !rentals.query.isError && !roomCards.query.isPending && !roomCards.query.isError

  const learningTitle =
    context?.learning_mode === "self"
      ? "我的学习"
      : context?.learning_mode === "family"
        ? "家庭信息"
        : "学习信息"
  const learningActionLabel = `查看${learningTitle}`
  const coachOption = coachQuery.data?.options[0]
  const effectivePermissions = new Set([
    ...(user?.permissions ?? []),
    ...(user?.scoped_permissions ?? []).filter((permission) =>
      ["housing.read", "teaching.read", "admission_cases.read", "assets.read", "procurement.read", "travel_reports.read", "enrollments.read", "accounts.read", "revenue_share.finance"].includes(permission),
    ),
  ])
  // A cached projection must never be shown while its capability is being revalidated.
  const departureData = !departures.query.isFetching && !departures.query.isError && departures.query.data?.available ? departures.query.data : undefined
  const rentalData = !rentals.query.isFetching && !rentals.query.isError && rentals.query.data?.available ? rentals.query.data : undefined
  const metrics = [
    ...(coachEnabled && !coachQuery.isFetching && !coachQuery.isError && coachQuery.data ? [{ label: "待查看学员", value: coachQuery.data.total, note: "下一期教练名单" }] : []),
    ...(learningEnabled && !studentsQuery.isFetching && !studentsQuery.isError && studentsQuery.data ? [{ label: "关联学员", value: studentsQuery.data.total, note: learningTitle }] : []),
    ...(contextReady && departureData ? [{ label: "今日退宿／结营", value: departureData.today_count, note: "计划日期 · 不代表实际离开" }] : []),
    ...(contextReady && rentalData ? [{ label: "租用到期提醒", value: rentalData.total, note: "近七日到期及已逾期" }] : []),
  ]

  function retryPortal(): void {
    void contextQuery.refetch()
    if (coachEnabled) void coachQuery.refetch()
    if (learningEnabled) void studentsQuery.refetch()
  }

  return (
    <div className="portalPage">
      <header className="portalPageHeader">
        <p className="pageEyebrow">WORKSPACE / 工作空间</p>
        <h1>工作台</h1>
        <p>今日工作与个人信息 · 从这里开始</p>
      </header>

      {metrics.length > 0 ? (
        <section className="portalSummary" aria-label="工作概览">
          {metrics.map((metric) => (
            <article key={metric.label}>
              <p>{metric.label}</p>
              <strong>{metric.value}<small>{metric.label === "租用到期提醒" ? " 项" : " 人"}</small></strong>
              <span>{metric.note}</span>
            </article>
          ))}
        </section>
      ) : null}

      {showEmpty ? (
        <section className="portalEmpty" aria-label="暂无工作项">
          <h2>当前暂无可用工作项</h2>
          <p>当前账号没有已授权的工作项。</p>
        </section>
      ) : accessError.permissionRevoked ? null : portalError ? (
        <section className="portalError" role="alert">
          <div>
            <strong>工作台暂时无法更新</strong>
            <p>{accessError.message}</p>
          </div>
          <button type="button" onClick={retryPortal}>
            <RefreshCw aria-hidden="true" />
            重试
          </button>
        </section>
      ) : privateDataFetching ? (
        <section className="portalLoading" aria-live="polite">
          <span className="portalLoadingBar" />
          <span className="portalLoadingBar short" />
          <p>正在更新工作台</p>
        </section>
      ) : noPortalTasks && hasBackOfficeCapability ? null : (
        <section className="portalTaskGrid" aria-label="门户工作项">
          {context?.invitations_available ? (
            <article className="portalTaskCard housing">
              <div className="portalTaskIcon">
                <BedDouble aria-hidden="true" />
              </div>
              <div className="portalTaskBody">
                <h2>我的邀请</h2>
                <p>受邀人员住宿摘要</p>
              </div>
              <Link to="/me/invitations">
                查看我的邀请
                <ArrowRight aria-hidden="true" />
              </Link>
            </article>
          ) : null}
          {context?.coach_available ? (
            <article className="portalTaskCard coach">
              <div className="portalTaskIcon">
                <ClipboardList aria-hidden="true" />
              </div>
              <div className="portalTaskBody">
                <p className="portalTaskLabel">教练工作</p>
                <h2>{coachOption?.cohort_names.join("、") ?? "暂无待开营班级"}</h2>
                <p>{coachQuery.data?.total ?? 0} 名待查看</p>
              </div>
              <Link to="/coach/next-cohorts" aria-label="查看下一期名单">
                查看名单
                <ArrowRight aria-hidden="true" />
              </Link>
            </article>
          ) : null}

          {context?.learning_available ? (
            <article className="portalTaskCard learning">
              <div className="portalTaskIcon">
                <BookOpenCheck aria-hidden="true" />
              </div>
              <div className="portalTaskBody">
                <p className="portalTaskLabel">学习信息</p>
                <h2>{learningTitle}</h2>
                <p>{studentsQuery.data?.total ?? 0} 名学员</p>
              </div>
              <Link to="/learning" aria-label={learningActionLabel}>
                查看学习信息
                <ArrowRight aria-hidden="true" />
              </Link>
            </article>
          ) : null}

          {enrollmentAvailable ? (
            <article className="portalTaskCard enrollment">
              <div className="portalTaskIcon">
                <ClipboardPenLine aria-hidden="true" />
              </div>
              <div className="portalTaskBody">
                <p className="portalTaskLabel">课程报名</p>
                <h2>学员报名</h2>
                <p>选择游戏项目与课程班型</p>
              </div>
              <Link to="/enrollment" aria-label="进入学员报名">
                选择班型
                <ArrowRight aria-hidden="true" />
              </Link>
            </article>
          ) : null}

          {context?.payments_available ? (
            <article className="portalTaskCard payment">
              <div className="portalTaskIcon">
                <WalletCards aria-hidden="true" />
              </div>
              <div className="portalTaskBody">
                <p className="portalTaskLabel">个人财务</p>
                <h2>本人付款</h2>
                <p>付款与退款记录</p>
              </div>
              <Link to="/me/payments" aria-label="查看本人付款">
                查看记录
                <ArrowRight aria-hidden="true" />
              </Link>
            </article>
          ) : null}

          {context?.housing_available ? (
            <article className="portalTaskCard housing">
              <div className="portalTaskIcon">
                <BedDouble aria-hidden="true" />
              </div>
              <div className="portalTaskBody">
                <p className="portalTaskLabel">个人住宿</p>
                <h2>住宿信息</h2>
                <p>当前住宿与未来预约</p>
              </div>
              <Link to="/me/housing" aria-label="查看住宿信息">
                查看住宿
                <ArrowRight aria-hidden="true" />
              </Link>
            </article>
          ) : null}

          {evaluationAvailable ? (
            <article className="portalTaskCard evaluation">
              <div className="portalTaskIcon">
                <MessageSquareText aria-hidden="true" />
              </div>
              <div className="portalTaskBody">
                <p className="portalTaskLabel">成长反馈</p>
                <h2>评价中心</h2>
                <p>系统评价与教学反馈</p>
              </div>
              <Link to="/evaluations" aria-label="进入评价中心">
                进入评价中心 <ArrowRight aria-hidden="true" />
              </Link>
            </article>
          ) : null}
        </section>
      )}

      <div className="portalReminders">
        <WorkspacePropertyReview enabled={propertyReviewEnabled} ready={contextReady} />
        <DepartureRemindersPanel state={departures} />
        <RentalRemindersPanel state={rentals} />
        <RoomCardRemindersContent state={roomCards} contextError={Boolean(contextQuery.error)} />
      </div>
      {contextReady && hasBackOfficeCapability ? (
        <DashboardPage
          embedded
          permissions={effectivePermissions}
          housingAvailable={context?.housing_management_available}
          isSuperAdmin={user?.is_super_admin}
        />
      ) : null}
    </div>
  )
}
