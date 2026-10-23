import { useQuery } from "@tanstack/react-query"
import {
  ArrowRight,
  BedDouble,
  BookOpenCheck,
  ClipboardList,
  MessageSquareText,
  RefreshCw,
  WalletCards,
} from "lucide-react"
import { Link } from "react-router-dom"

import { useAuth } from "../auth/AuthProvider"
import { DashboardPage } from "../layout/AppShell"
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
])

export function PortalDashboardPage() {
  const { user } = useAuth()
  const accountId = user?.account_id ?? "anonymous"
  const contextQuery = usePortalContext()
  const context = contextQuery.data
  const contextReady = Boolean(context && !contextQuery.isFetching)
  const coachEnabled = contextReady && context?.coach_available === true
  const learningEnabled = contextReady && context?.learning_available === true

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
  const hasPortalCapability = Boolean(
    context?.coach_available ||
      context?.learning_available ||
      context?.payments_available ||
      context?.housing_available,
  )
  const hasBackOfficeCapability = Boolean(
    user?.permissions.some((permission) => BACK_OFFICE_PERMISSIONS.has(permission)) ||
      user?.scoped_permissions?.some((permission) =>
        BACK_OFFICE_PERMISSIONS.has(permission),
      ),
  )
  const evaluationAvailable =
    user?.permissions.includes("evaluation_center.access") === true
  const privateDataFetching =
    contextQuery.isFetching ||
    (coachEnabled && coachQuery.isFetching) ||
    (learningEnabled && studentsQuery.isFetching)

  if (
    !privateDataFetching &&
    !portalError &&
    !hasPortalCapability &&
    !evaluationAvailable
  ) {
    if (hasBackOfficeCapability) return <DashboardPage />
    return (
      <div className="portalPage">
        <header className="portalPageHeader">
          <p className="pageEyebrow">WORKSPACE</p>
          <h1>工作台</h1>
        </header>
        <section className="portalEmpty" aria-label="暂无工作项">
          <h2>当前暂无可用工作项</h2>
          <p>当前账号没有已授权的工作项。</p>
        </section>
      </div>
    )
  }

  const learningTitle =
    context?.learning_mode === "self"
      ? "我的学习"
      : context?.learning_mode === "family"
        ? "家庭信息"
        : "学习信息"
  const learningActionLabel = `查看${learningTitle}`
  const coachOption = coachQuery.data?.options[0]

  function retryPortal(): void {
    void contextQuery.refetch()
    if (coachEnabled) void coachQuery.refetch()
    if (learningEnabled) void studentsQuery.refetch()
  }

  return (
    <div className="portalPage">
      <header className="portalPageHeader">
        <p className="pageEyebrow">TODAY</p>
        <h1>工作台</h1>
        <p>今日工作与个人信息</p>
      </header>

      {accessError.permissionRevoked ? null : portalError ? (
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
      ) : (
        <section className="portalTaskGrid" aria-label="门户工作项">
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

      {hasBackOfficeCapability ? <DashboardPage embedded /> : null}
    </div>
  )
}
