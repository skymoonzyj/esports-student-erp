import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import type { FormEvent, ReactNode } from "react"

import { listAuditEvents } from "./api"
import type { AuditFilters, AuditMetadata, AuditOutcome } from "./api"
import "./settings.css"

const phonePattern = /(?<!\d)1(?:[^\dA-Za-z]{0,3}\d){10}(?!\d)/
const identityPattern = /(?<!\d)(?:\d[^\dA-Za-z]{0,3}){17}[0-9Xx](?![0-9A-Za-z])/
const secretAssignmentPattern =
  /(?:password|temporary[_ -]?password|token|bearer|csrf|cipher(?:text)?|密码|临时密码|令牌)\s*(?::|：|=|\s)\s*\S{8,}/i
const rawSecretPattern = /(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])/g
const canonicalUuidPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function safeAuditText(value: string | null): string {
  if (!value) return "未填写"
  const rawSecret = [...value.matchAll(rawSecretPattern)].some((match) => {
    const candidate = match[0]
    const classes = [
      /[a-z]/.test(candidate),
      /[A-Z]/.test(candidate),
      /\d/.test(candidate),
      /[_-]/.test(candidate),
    ].filter(Boolean).length
    return classes >= 3
  })
  if (
    phonePattern.test(value) ||
    identityPattern.test(value) ||
    secretAssignmentPattern.test(value) ||
    (!canonicalUuidPattern.test(value) && rawSecret)
  ) {
    return "敏感内容已隐藏"
  }
  return value
}

function nextCalendarDate(value: string): string {
  const [year, month, day] = value.split("-").map(Number)
  const next = new Date(Date.UTC(year, month - 1, day + 1))
  return next.toISOString().slice(0, 10)
}

function metadataItems(metadata: AuditMetadata): ReactNode[] {
  const items: ReactNode[] = []
  if (metadata.field === "identity_number") {
    items.push(<span key="field">身份证字段</span>)
  } else if (metadata.field === "phone") {
    items.push(<span key="field">手机号字段</span>)
  }
  if (metadata.error_code === "decrypt_failed") {
    items.push(<span key="error">解密失败</span>)
  }
  if (
    typeof metadata.result_count === "number" &&
    Number.isInteger(metadata.result_count) &&
    metadata.result_count >= 0
  ) {
    items.push(<span key="count">结果数量：{metadata.result_count}</span>)
  }
  if (typeof metadata.role_key === "string") {
    items.push(<span key="role">角色：{safeAuditText(metadata.role_key)}</span>)
  }
  if (typeof metadata.permission_key === "string") {
    items.push(<span key="permission">权限：{safeAuditText(metadata.permission_key)}</span>)
  }
  if (metadata.effect === "allow" || metadata.effect === "deny") {
    items.push(<span key="effect">结果：{metadata.effect === "allow" ? "允许" : "拒绝"}</span>)
  }
  if (typeof metadata.scope_kind === "string") {
    const labels: Record<string, string> = {
      global: "全部数据",
      self: "本人",
      family: "本人家庭",
      person: "指定人员",
      assigned_class: "指定班级",
    }
    const scopeId =
      typeof metadata.scope_id === "string" ? ` · ${safeAuditText(metadata.scope_id)}` : ""
    items.push(
      <span key="scope">
        范围：{labels[metadata.scope_kind] ?? "未知范围"}{scopeId}
      </span>,
    )
  }
  return items
}

function AuditMetadataView({ metadata }: { metadata: AuditMetadata }) {
  const items = metadataItems(metadata)
  return <>{items.length > 0 ? items : <span>无公开元数据</span>}</>
}

const outcomeLabels: Record<AuditOutcome, string> = {
  allowed: "已允许",
  denied: "已拒绝",
  failed: "执行失败",
}

export function AuditPage() {
  const [actorUserId, setActorUserId] = useState("")
  const [action, setAction] = useState("")
  const [resourceType, setResourceType] = useState("")
  const [dateFrom, setDateFrom] = useState("")
  const [dateTo, setDateTo] = useState("")
  const [filters, setFilters] = useState<AuditFilters>({ page: 1, pageSize: 20 })
  const events = useQuery({
    queryKey: ["audit-events", filters],
    queryFn: () => listAuditEvents(filters),
  })

  function search(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    setFilters({
      actorUserId: actorUserId.trim() || undefined,
      action: action.trim() || undefined,
      resourceType: resourceType.trim() || undefined,
      dateFrom: dateFrom ? `${dateFrom}T00:00:00+08:00` : undefined,
      dateTo: dateTo ? `${nextCalendarDate(dateTo)}T00:00:00+08:00` : undefined,
      page: 1,
      pageSize: 20,
    })
  }

  return (
    <div className="settingsPage auditPage">
      <div className="settingsHeading">
        <div>
          <p className="pageEyebrow">APPEND-ONLY AUDIT</p>
          <h1>安全审计</h1>
          <p>查看敏感访问与管理操作记录，页面只展示经过白名单过滤的内容。</p>
        </div>
      </div>

      <form className="auditFilters" onSubmit={search}>
        <div className="formField">
          <label htmlFor="audit-actor">操作人账号 ID</label>
          <input
            id="audit-actor"
            value={actorUserId}
            onChange={(event) => setActorUserId(event.target.value)}
          />
        </div>
        <div className="formField">
          <label htmlFor="audit-action">动作</label>
          <input
            id="audit-action"
            value={action}
            onChange={(event) => setAction(event.target.value)}
          />
        </div>
        <div className="formField">
          <label htmlFor="audit-resource">资源类型</label>
          <input
            id="audit-resource"
            value={resourceType}
            onChange={(event) => setResourceType(event.target.value)}
          />
        </div>
        <div className="formField">
          <label htmlFor="audit-date-from">开始日期</label>
          <input
            id="audit-date-from"
            type="date"
            value={dateFrom}
            onChange={(event) => setDateFrom(event.target.value)}
          />
        </div>
        <div className="formField">
          <label htmlFor="audit-date-to">结束日期</label>
          <input
            id="audit-date-to"
            type="date"
            value={dateTo}
            onChange={(event) => setDateTo(event.target.value)}
          />
        </div>
        <button className="primaryButton" type="submit">
          查询审计
        </button>
      </form>

      {events.isLoading ? <div className="settingsState">正在读取审计记录…</div> : null}
      {events.isError ? <div className="settingsState">暂时无法读取审计记录。</div> : null}
      {events.data?.items.length === 0 ? <div className="settingsState">没有匹配记录。</div> : null}
      {events.data?.items.length ? (
        <section className="settingsTable auditTable" role="table" aria-label="审计事件">
          <div className="settingsTableHeader auditRow" role="row">
            <span role="columnheader">时间 / 结果</span>
            <span role="columnheader">动作 / 资源</span>
            <span role="columnheader">用途</span>
            <span role="columnheader">安全元数据</span>
          </div>
          {events.data.items.map((event) => (
            <article className="settingsTableRow auditRow" role="row" key={event.id}>
              <div role="cell" data-label="时间 / 结果">
                <strong>{new Date(event.created_at).toLocaleString("zh-CN")}</strong>
                <small>{event.actor_user_id ?? "系统任务"}</small>
                <span className={`statusPill ${event.outcome}`}>
                  {outcomeLabels[event.outcome]}
                </span>
              </div>
              <div role="cell" data-label="动作 / 资源">
                <strong>{safeAuditText(event.action)}</strong>
                <small>
                  {safeAuditText(event.resource_type)} · {event.resource_id ?? "无资源 ID"}
                </small>
              </div>
              <div role="cell" data-label="用途">
                {safeAuditText(event.purpose)}
              </div>
              <div className="auditMetadata" role="cell" data-label="安全元数据">
                <AuditMetadataView metadata={event.metadata} />
              </div>
            </article>
          ))}
        </section>
      ) : null}
    </div>
  )
}
