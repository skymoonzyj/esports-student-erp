import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"

import { useAuth } from "../auth/AuthProvider"
import { getSurveyPrint } from "./surveyApi"
import { normalizeSurveyFilters, surveyKeys, type SurveyListFilters } from "./surveyKeys"
import {
  canPrintSurvey,
  canPrintSurveyIdentity,
  purgeSensitiveSurveyQueries,
} from "./surveyIdentityCapability"
import { useSurveyAccessRevocation } from "./useSurveyAccessRevocation"
import "./camp-surveys.css"

export function CampSurveyPrintPage() {
  const { user } = useAuth()
  const accountId = user?.account_id ?? "anonymous"
  const queryClient = useQueryClient()
  const printCapability = canPrintSurvey(user)
  const identityCapability = canPrintSurveyIdentity(user)
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedIdentity = searchParams.get("identity") === "real_name" ? "real_name" : "hidden"
  const effectiveIdentity = identityCapability && requestedIdentity === "real_name"
    ? "real_name"
    : "hidden"
  const initialFilters = normalizeSurveyFilters({
    cohortId: searchParams.get("cohort_id"),
    projectId: searchParams.get("project_id"),
    staffPersonId: searchParams.get("staff_id"),
    roleKey: searchParams.get("role_key"),
    identity: effectiveIdentity,
  })
  const [draft, setDraft] = useState<SurveyListFilters>(initialFilters)
  const filters = normalizeSurveyFilters({
    cohortId: searchParams.get("cohort_id"),
    projectId: searchParams.get("project_id"),
    staffPersonId: searchParams.get("staff_id"),
    roleKey: searchParams.get("role_key"),
    identity: effectiveIdentity,
  })

  useEffect(() => {
    if (identityCapability || requestedIdentity !== "real_name") return
    const nextParams = new URLSearchParams(searchParams)
    nextParams.set("identity", "hidden")
    setSearchParams(nextParams, { replace: true })
  }, [identityCapability, requestedIdentity, searchParams, setSearchParams])

  useEffect(() => {
    if (identityCapability || !user?.account_id) return
    purgeSensitiveSurveyQueries(queryClient, user.account_id)
  }, [identityCapability, queryClient, user?.account_id])

  const query = useQuery({
    queryKey: surveyKeys.print(accountId, filters),
    queryFn: ({ signal }) => getSurveyPrint(filters, signal),
    enabled: Boolean(user?.account_id),
    staleTime: filters.identity === "real_name" ? 0 : undefined,
    gcTime: filters.identity === "real_name" ? 0 : undefined,
    refetchOnWindowFocus: "always",
    retry: false,
  })
  useSurveyAccessRevocation({
    accountId: user?.account_id,
    errors: [query.error],
    onRevoke: () => {
      setDraft((current) => ({ ...current, identity: "hidden" }))
    },
  })

  function applyFilters(): void {
    const next = normalizeSurveyFilters(draft)
    const nextParams = new URLSearchParams()
    if (next.cohortId) nextParams.set("cohort_id", next.cohortId)
    if (next.projectId) nextParams.set("project_id", next.projectId)
    if (next.staffPersonId) nextParams.set("staff_id", next.staffPersonId)
    if (next.roleKey) nextParams.set("role_key", next.roleKey)
    nextParams.set("identity", identityCapability ? next.identity : "hidden")
    setSearchParams(nextParams)
  }

  function setIdentity(checked: boolean): void {
    const identity = checked && identityCapability ? "real_name" : "hidden"
    setDraft((current) => ({ ...current, identity }))
    const nextParams = new URLSearchParams(searchParams)
    nextParams.set("identity", identity)
    setSearchParams(nextParams)
  }

  if (query.isPending) {
    return <main className="campSurveyPage"><p aria-live="polite">正在生成打印投影…</p></main>
  }
  if (query.isError) {
    return (
      <main className="campSurveyPage">
        <p role="alert">无权打印评价。</p>
        <button type="button" onClick={() => void query.refetch()}>重试</button>
      </main>
    )
  }

  return (
    <main className="campSurveyPage printProjection">
      <header className="campSurveyPrintToolbar">
          <Link to="/evaluations/surveys/admin">返回管理</Link>
        <p className="pageEyebrow">PRINT PROJECTION</p>
        <h1>营期评价打印</h1>
        <div className="campSurveyPrintControls">
          {identityCapability ? (
            <label className="campSurveyCheckboxLabel">
              <input type="checkbox" aria-label="打印实名" checked={filters.identity === "real_name"} onChange={(event) => setIdentity(event.currentTarget.checked)} />
              打印实名
            </label>
          ) : null}
          {printCapability ? <button type="button" onClick={() => window.print()}>打印</button> : null}
        </div>
      </header>
      <form className="campSurveyFilters campSurveyPrintFilters" onSubmit={(event) => { event.preventDefault(); applyFilters() }}>
        <label>届次 ID<input aria-label="打印届次 ID" value={draft.cohortId ?? ""} onChange={(event) => setDraft({ ...draft, cohortId: event.currentTarget.value || null })} /></label>
        <label>项目 ID<input aria-label="打印项目 ID" value={draft.projectId ?? ""} onChange={(event) => setDraft({ ...draft, projectId: event.currentTarget.value || null })} /></label>
        <label>工作人员 ID<input aria-label="打印工作人员 ID" value={draft.staffPersonId ?? ""} onChange={(event) => setDraft({ ...draft, staffPersonId: event.currentTarget.value || null })} /></label>
        <label>角色 key<input aria-label="打印角色 key" value={draft.roleKey ?? ""} onChange={(event) => setDraft({ ...draft, roleKey: event.currentTarget.value || null })} /></label>
        <button type="submit">应用打印筛选</button>
      </form>
      <p>身份模式：{filters.identity === "real_name" && query.data.identity === "real_name" ? "实名" : "隐藏"}</p>
      <p aria-label="当前打印范围">
        打印范围：{filters.cohortId || "全部届次"} · {filters.projectId || "全部项目"} · {filters.staffPersonId || "全部工作人员"}{filters.roleKey ? ` · 角色 ${filters.roleKey}` : ""}
      </p>
      {query.data.items.length === 0 ? <p>暂无可打印的评价。</p> : null}
      {query.data.items.map((item) => (
        <article key={`${item.cohort_id}-${item.project_id ?? "all"}-${item.staff_person_id ?? item.role_key}`}>
          <h2>{item.role_name}{item.staff_name ? ` · ${item.staff_name}` : ""}</h2>
          <p>项目：{item.project_id ?? "全部项目"}</p>
          <p>{`平均评分 ${item.average_score}（${item.item_count} 条）`}</p>
          {item.roles.length > 0 ? <p>服务角色：{item.roles.map((role) => `${role.role_name}（${role.role_category}）`).join("、")}</p> : null}
          {item.service_intervals.length > 0 ? (
            <section aria-label="服务区间">
              <h3>服务区间</h3>
              {item.service_intervals.map((interval, index) => <p key={`${index}-${JSON.stringify(interval)}`}>{Object.entries(interval).map(([key, value]) => `${key}：${String(value)}`).join(" · ")}</p>)}
            </section>
          ) : null}
          {item.team_suggestions.length > 0 ? (
            <section aria-label="教学团队建议">
              <h3>教学团队建议</h3>
              {item.team_suggestions.map((suggestion) => <p key={suggestion}>{suggestion}</p>)}
            </section>
          ) : null}
          <section aria-label="匿名评分明细">
            {item.details.map((detail) => (
              <div className="campSurveyPrintDetail" key={detail.ordinal}>
                <p>
                  #{detail.ordinal} · 评分 {detail.score}/10
                  {filters.identity === "real_name" && query.data.identity === "real_name" && detail.respondent_name
                    ? ` · 评价者：${detail.respondent_name}`
                    : " · 匿名评价者"}
                </p>
                {detail.reason ? <p>原因：{detail.reason}</p> : null}
                {detail.suggestion ? <p>建议：{detail.suggestion}</p> : null}
              </div>
            ))}
          </section>
        </article>
      ))}
    </main>
  )
}
