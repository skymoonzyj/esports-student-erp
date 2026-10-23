import {
  ArrowRight,
  ClipboardPenLine,
  Database,
  Gamepad2,
  MessageSquareText,
  MonitorUp,
  RefreshCw,
} from "lucide-react"
import type { LucideIcon } from "lucide-react"
import { Link } from "react-router-dom"

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

function DisabledEvaluationBranch({
  title,
  icon: Icon,
}: {
  title: string
  icon: LucideIcon
}) {
  return (
    <article className="evaluationBranch disabled">
      <Icon aria-hidden="true" />
      <div>
        <h2>{title}</h2>
        <p>该评价分支将在后续开发阶段开放。</p>
      </div>
      <span>尚未开放</span>
    </article>
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

  const showAdmin = Object.values(context.data.admin_sections).some(Boolean)

  return (
    <div className="evaluationPage">
      <header className="evaluationPageHeader">
        <p className="pageEyebrow">EVALUATIONS</p>
        <h1>评价中心</h1>
        <p>系统评价与教学反馈</p>
      </header>
      <section className="evaluationBranchGrid" aria-label="评价类型">
        <article className="evaluationBranch active">
          <MonitorUp aria-hidden="true" />
          <div>
            <h2>系统评价</h2>
            <p>截图上传和评分处理将在后续单元启用</p>
          </div>
          <Link to="/evaluations/system" aria-label="打开系统评价">
            打开系统评价 <ArrowRight aria-hidden="true" />
          </Link>
        </article>
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
        <DisabledEvaluationBranch title="教学评语" icon={MessageSquareText} />
        <DisabledEvaluationBranch title="营期评价" icon={ClipboardPenLine} />
      </section>
    </div>
  )
}
