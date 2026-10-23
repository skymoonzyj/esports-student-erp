import { useQuery } from "@tanstack/react-query"
import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { Link } from "react-router-dom"

import { useAuth } from "../auth/AuthProvider"
import { getAdminSurveyFeedback } from "./surveyApi"
import { surveyKeys, type SurveyListFilters } from "./surveyKeys"
import {
  canPrintSurvey,
  canReadSurveyIdentity,
  purgeSensitiveSurveyQueries,
} from "./surveyIdentityCapability"
import { useSurveyAccessRevocation } from "./useSurveyAccessRevocation"
import "./camp-surveys.css"

export function CampSurveyAdminPage() {
  const { user } = useAuth()
  const accountId = user?.account_id ?? "anonymous"
  const queryClient = useQueryClient()
  const identityCapability = canReadSurveyIdentity(user)
  const printCapability = canPrintSurvey(user)
  const [draft, setDraft] = useState<SurveyListFilters>({ identity: "hidden" })
  const [filters, setFilters] = useState<SurveyListFilters>({ identity: "hidden" })
  const effectiveFilters = identityCapability
    ? filters
    : { ...filters, identity: "hidden" as const }
  const currentPage = effectiveFilters.page ?? 1
  const pageSize = effectiveFilters.pageSize ?? 50

  useEffect(() => {
    if (identityCapability || !user?.account_id) return
    setDraft((current) => current.identity === "hidden" ? current : { ...current, identity: "hidden" })
    setFilters((current) => current.identity === "hidden" ? current : { ...current, identity: "hidden" })
    purgeSensitiveSurveyQueries(queryClient, user.account_id)
  }, [identityCapability, queryClient, user?.account_id])

  const query = useQuery({
    queryKey: surveyKeys.admin(accountId, effectiveFilters),
    queryFn: ({ signal }) => getAdminSurveyFeedback(effectiveFilters, signal),
    enabled: Boolean(user?.account_id),
    staleTime: effectiveFilters.identity === "real_name" ? 0 : undefined,
    gcTime: effectiveFilters.identity === "real_name" ? 0 : undefined,
    refetchOnWindowFocus: "always",
    retry: false,
  })
  useSurveyAccessRevocation({
    accountId: user?.account_id,
    errors: [query.error],
    onRevoke: () => {
      setDraft({ identity: "hidden" })
      setFilters({ identity: "hidden" })
    },
  })

  function applyFilters(): void {
    setFilters({ ...draft, identity: identityCapability ? draft.identity : "hidden", page: 1 })
  }

  function setIdentity(checked: boolean): void {
    const identity = checked ? "real_name" : "hidden"
    setDraft((current) => ({ ...current, identity }))
    setFilters((current) => ({ ...current, identity, page: 1 }))
  }

  function printHref(): string {
    const params = new URLSearchParams()
    if (filters.cohortId) params.set("cohort_id", filters.cohortId)
    if (filters.projectId) params.set("project_id", filters.projectId)
    if (filters.staffPersonId) params.set("staff_id", filters.staffPersonId)
    if (filters.roleKey) params.set("role_key", filters.roleKey)
    params.set("identity", effectiveFilters.identity ?? "hidden")
    const query = params.toString()
    return query ? `/evaluations/surveys/print?${query}` : "/evaluations/surveys/print"
  }

  if (query.isPending) {
    return <main className="campSurveyPage"><p aria-live="polite">正在加载评价汇总…</p></main>
  }
  if (query.isError) {
    return (
      <main className="campSurveyPage">
        <p role="alert">无权访问或数据暂时不可用。</p>
        <button type="button" onClick={() => void query.refetch()}>重试</button>
      </main>
    )
  }

  return (
    <main className="campSurveyPage">
      <header>
        <Link to="/evaluations">返回评价中心</Link>
        <p className="pageEyebrow">SURVEY ADMIN</p>
        <h1>营期评价管理</h1>
        <p>默认匿名展示；实名读取由服务端按当前范围重新判权。</p>
      </header>
      <form className="campSurveyFilters" onSubmit={(event) => { event.preventDefault(); applyFilters() }}>
        <label>届次 ID<input aria-label="届次 ID" value={draft.cohortId ?? ""} onChange={(event) => setDraft({ ...draft, cohortId: event.currentTarget.value || null })} /></label>
        <label>项目 ID<input aria-label="项目 ID" value={draft.projectId ?? ""} onChange={(event) => setDraft({ ...draft, projectId: event.currentTarget.value || null })} /></label>
        <label>工作人员 ID<input aria-label="工作人员 ID" value={draft.staffPersonId ?? ""} onChange={(event) => setDraft({ ...draft, staffPersonId: event.currentTarget.value || null })} /></label>
        <label>角色 key<input aria-label="角色 key" value={draft.roleKey ?? ""} onChange={(event) => setDraft({ ...draft, roleKey: event.currentTarget.value || null })} /></label>
        <button type="submit">应用筛选</button>
        {identityCapability ? (
          <label className="campSurveyCheckboxLabel">
            <input type="checkbox" aria-label="查看实名" checked={filters.identity === "real_name"} onChange={(event) => setIdentity(event.currentTarget.checked)} />
            查看实名
          </label>
        ) : null}
      </form>
      <div className="campSurveyAdminActions">
        {printCapability ? <Link to={printHref()}>打开打印投影</Link> : null}
        <span>当前模式：{effectiveFilters.identity === "real_name" ? "实名" : "匿名"}</span>
      </div>
      {query.data.items.length === 0 ? <p>没有符合筛选条件的评价。</p> : null}
      <section className="campSurveyTable" aria-label="营期评价明细">
        {query.data.items.map((item) => (
          <article key={`${item.version_id}-${item.ordinal}`}>
            <header>
              <strong>
                {effectiveFilters.identity === "real_name"
                  ? `${item.respondent_name ?? item.respondent_label} · ${item.staff_name ?? "工作人员"}`
                  : `${item.staff_name ?? "工作人员"} · 匿名评价 #${item.ordinal}`}
              </strong>
              <span>{item.role_name ?? item.role_category ?? "工作人员"}</span>
            </header>
            <p className="campSurveyScore">评分 {item.score}/10</p>
            {item.reason ? <p>原因：{item.reason}</p> : null}
            {item.suggestion ? <p>建议：{item.suggestion}</p> : null}
            {effectiveFilters.identity === "real_name" ? (
              <div className="campSurveyAdminOriginal" aria-label="完整原文">
                {item.course_training_score !== null ? <p>课程训练：{item.course_training_score}/10{item.course_training_reason ? `（${item.course_training_reason}）` : ""}</p> : null}
                {item.life_service_score !== null ? <p>住宿生活：{item.life_service_score}/10{item.life_service_reason ? `（${item.life_service_reason}）` : ""}</p> : null}
                {item.service_management_score !== null ? <p>服务管理：{item.service_management_score}/10{item.service_management_reason ? `（${item.service_management_reason}）` : ""}</p> : null}
                {item.overall_suggestion ? <p>整体建议：{item.overall_suggestion}</p> : null}
                {item.favorite_dishes?.length ? <p>喜欢的菜：{item.favorite_dishes.join("、")}</p> : null}
              </div>
            ) : null}
          </article>
        ))}
      </section>
      <nav className="campSurveyPagination" aria-label="营期评价分页">
        <button type="button" disabled={currentPage === 1} onClick={() => setFilters((current) => ({ ...current, page: Math.max(1, currentPage - 1), pageSize }))}>上一页评价</button>
        <span>第 {currentPage} 页，共 {query.data.total} 条</span>
        <button type="button" disabled={currentPage * pageSize >= query.data.total} onClick={() => setFilters((current) => ({ ...current, page: currentPage + 1, pageSize }))}>下一页评价</button>
      </nav>
    </main>
  )
}
