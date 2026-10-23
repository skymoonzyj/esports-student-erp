import {
  ArrowRight,
  ClipboardPenLine,
  Database,
  Gamepad2,
  MessageSquareText,
  MonitorUp,
  RefreshCw,
} from "lucide-react"
import { Link } from "react-router-dom"

import { SystemEvaluationDashboard } from "./SystemEvaluationDashboard"
import { useEvaluationAccessError } from "./useEvaluationAccessError"
import { useEvaluationContext } from "./useEvaluationContext"

import "./evaluations.css"

function EvaluationLoading({ label }: { label: string }) {
  return (
    <section className="evaluationLoading" aria-live="polite">
      <span className="evaluationLoadingBar" />
      <span className="evaluationLoadingBar short" />
      <p>{label}</p>
    </section>
  )
}

function EvaluationError({
  message,
  onRetry,
}: {
  message: string
  onRetry: () => void
}) {
  return (
    <section className="evaluationError" role="alert">
      <div>
        <strong>评价中心暂时无法更新</strong>
        <p>{message}</p>
      </div>
      <button type="button" onClick={onRetry}>
        <RefreshCw aria-hidden="true" />
        重试
      </button>
    </section>
  )
}

export function EvaluationCenterPage() {
  const context = useEvaluationContext()

  const accessError = useEvaluationAccessError(context.error)
  if (context.isPending || context.isFetching || accessError.permissionRevoked) {
    return <EvaluationLoading label="正在更新评价中心" />
  }
  if (context.isError) {
    return (
      <EvaluationError
        message={accessError.message}
        onRetry={() => void context.refetch()}
      />
    )
  }

  const showSystemEvaluation = context.data.system_evaluation_available
  const showAdmin =
    context.data.admin_available &&
    Object.values(context.data.admin_sections).some(Boolean)
  const surveyEntryLinks = [
    context.data.survey_sections.respondent
      ? { href: "/evaluations/surveys", label: "填写营期问卷" }
      : null,
    context.data.survey_sections.staff_feedback
      ? {
          href: "/evaluations/surveys/feedback",
          label: "查看收到的匿名反馈",
        }
      : null,
    context.data.survey_sections.administration
      ? { href: "/evaluations/surveys/admin", label: "管理营期评价" }
      : null,
  ].filter((entry): entry is { href: string; label: string } => entry !== null)
  const hasAvailableEvaluation =
    showSystemEvaluation ||
    context.data.game_accounts_available ||
    showAdmin ||
    context.data.reports_available ||
    surveyEntryLinks.length > 0

  return (
    <div className="evaluationPage">
      <header className="evaluationPageHeader">
        <p className="pageEyebrow">EVALUATIONS</p>
        <h1>评价中心</h1>
        <p>系统评价与教学反馈</p>
      </header>
      {showSystemEvaluation ? <SystemEvaluationDashboard /> : null}
      {hasAvailableEvaluation ? (
        <section className="evaluationBranchGrid" aria-label="评价类型">
          {showSystemEvaluation ? (
            <article className="evaluationBranch active">
              <MonitorUp aria-hidden="true" />
              <div>
                <h2>系统评价</h2>
                <p>上传截图，并查看 ERP 任务处理与已发布结果。</p>
              </div>
              <Link to="/evaluations/system" aria-label="打开系统评价">
                打开系统评价 <ArrowRight aria-hidden="true" />
              </Link>
            </article>
          ) : null}
          {context.data.game_accounts_available ? (
            <article className="evaluationBranch active">
              <Gamepad2 aria-hidden="true" />
              <div>
                <h2>我的游戏账号</h2>
                <p>维护本人或当前获权家庭学员的项目游戏账号。</p>
              </div>
              <Link to="/evaluations/game-accounts" aria-label="我的游戏账号">
                我的游戏账号 <ArrowRight aria-hidden="true" />
              </Link>
            </article>
          ) : null}
          {showAdmin ? (
            <article className="evaluationBranch active">
              <Database aria-hidden="true" />
              <div>
                <h2>基础数据管理</h2>
                <p>管理工作人员、学员匹配资料与分析配置版本。</p>
              </div>
              <Link to="/evaluations/admin" aria-label="基础数据管理">
                基础数据管理 <ArrowRight aria-hidden="true" />
              </Link>
            </article>
          ) : null}
          {context.data.reports_available ? (
            <article className="evaluationBranch active">
              <MessageSquareText aria-hidden="true" />
              <div>
                <h2>教学评语</h2>
                <p>填写日报、周报，或进入已授权的教学评语审核与查看入口。</p>
              </div>
              <Link to="/evaluations/reports" aria-label="进入教学评语">
                进入教学评语 <ArrowRight aria-hidden="true" />
              </Link>
            </article>
          ) : null}
          {surveyEntryLinks.length > 0 ? (
            <article className="evaluationBranch active">
              <ClipboardPenLine aria-hidden="true" />
              <div>
                <h2>营期评价</h2>
                <p>提交结营问卷，查看与本人相关的匿名工作人员反馈。</p>
              </div>
              <div className="evaluationSurveyLinks">
                {surveyEntryLinks.map(({ href, label }) => (
                  <Link key={href} to={href} aria-label={label}>
                    {label} <ArrowRight aria-hidden="true" />
                  </Link>
                ))}
              </div>
            </article>
          ) : null}
        </section>
      ) : (
        <section className="evaluationEmpty" aria-live="polite">
          <h2>暂无可用评价</h2>
          <p>当前账号暂未获得可使用的评价项目。</p>
        </section>
      )}
    </div>
  )
}
