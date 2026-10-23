import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import { Link } from "react-router-dom"

import { useAuth } from "../auth/AuthProvider"
import { getMySurveyFeedback } from "./surveyApi"
import { surveyKeys, type SurveyListFilters } from "./surveyKeys"
import { useSurveyAccessRevocation } from "./useSurveyAccessRevocation"
import "./camp-surveys.css"

export function MySurveyFeedbackPage() {
  const { user } = useAuth()
  const accountId = user?.account_id ?? "anonymous"
  const [draft, setDraft] = useState<SurveyListFilters>({})
  const [filters, setFilters] = useState<SurveyListFilters>({})
  const [page, setPage] = useState(1)
  const pageSize = 50
  const query = useQuery({
    queryKey: surveyKeys.feedback(accountId, page, pageSize, filters),
    queryFn: ({ signal }) => getMySurveyFeedback(page, pageSize, signal, filters),
    enabled: Boolean(user?.account_id),
    refetchOnWindowFocus: "always",
    retry: false,
  })
  useSurveyAccessRevocation({
    accountId: user?.account_id,
    errors: [query.error],
    onRevoke: () => {
      setDraft({})
      setFilters({})
      setPage(1)
    },
  })

  if (query.isPending) {
    return <main className="campSurveyPage"><p aria-live="polite">正在加载反馈…</p></main>
  }
  if (query.isError) {
    return (
      <main className="campSurveyPage">
        <p role="alert">反馈暂时不可用。</p>
        <button type="button" onClick={() => void query.refetch()}>重试</button>
      </main>
    )
  }

  return (
    <main className="campSurveyPage">
      <header>
        <Link to="/evaluations">返回评价中心</Link>
        <p className="pageEyebrow">STAFF FEEDBACK</p>
        <h1>收到的匿名反馈</h1>
        <p>反馈默认匿名，仅展示与您相关的评价；与角色无关的总体字段不会显示。</p>
      </header>
      <form className="campSurveyFilters" onSubmit={(event) => { event.preventDefault(); setPage(1); setFilters({ ...draft }) }}>
        <label>届次 ID<input aria-label="反馈届次 ID" value={draft.cohortId ?? ""} onChange={(event) => setDraft({ ...draft, cohortId: event.currentTarget.value || null })} /></label>
        <label>角色 ID<input aria-label="反馈角色 ID" value={draft.roleId ?? ""} onChange={(event) => setDraft({ ...draft, roleId: event.currentTarget.value || null })} /></label>
        <label>角色 key<input aria-label="反馈角色 key" value={draft.roleKey ?? ""} onChange={(event) => setDraft({ ...draft, roleKey: event.currentTarget.value || null })} /></label>
        <button type="submit">应用筛选</button>
      </form>
      {query.data.items.length === 0 ? <p>暂时没有收到匿名反馈。</p> : null}
      <section className="campSurveyTable" aria-label="匿名工作人员反馈">
        {query.data.items.map((item) => (
          <article key={`${item.cycle_id}-${item.version}-${item.ordinal}`}>
            <header>
              <strong>{item.role_name ?? item.role_key ?? item.role_category}</strong>
              <span>第 {item.ordinal} 条 · V{item.version}</span>
            </header>
            {item.project_id ? <p>项目：{item.project_id}</p> : null}
            <p className="campSurveyScore">评分 {item.score}/10</p>
            {item.reason ? <p>原因：{item.reason}</p> : <p className="campSurveyMuted">未填写评分原因</p>}
            {item.suggestion ? <p>给工作人员的建议：{item.suggestion}</p> : null}
            {item.course_training_score !== null ? <p>课程训练总体评分：{item.course_training_score}/10</p> : null}
            {item.course_training_reason ? <p>课程训练评分原因：{item.course_training_reason}</p> : null}
            {item.life_service_score !== null ? <p>住宿生活总体评分：{item.life_service_score}/10</p> : null}
            {item.life_service_reason ? <p>住宿生活评分原因：{item.life_service_reason}</p> : null}
            {item.service_management_score !== null ? <p>服务管理总体评分：{item.service_management_score}/10</p> : null}
            {item.service_management_reason ? <p>服务管理评分原因：{item.service_management_reason}</p> : null}
            {item.team_suggestion ? <p>给教学团队的建议：{item.team_suggestion}</p> : null}
          </article>
        ))}
      </section>
      <nav className="campSurveyPagination" aria-label="匿名反馈分页">
        <button type="button" disabled={page === 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>上一页反馈</button>
        <span>第 {page} 页，共 {query.data.total} 条</span>
        <button type="button" disabled={page * pageSize >= query.data.total} onClick={() => setPage((value) => value + 1)}>下一页反馈</button>
      </nav>
    </main>
  )
}
