import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import type { FormEvent } from "react"

import { ProblemDetailsError } from "../api/client"
import { useOptionalAuth } from "../auth/AuthProvider"
import { formatAuditDateTime } from "../shared/date"
import { getOperationsRequestErrors, operationsKeys } from "./api"
import type { OperationsRequestError, OperationsRequestErrorFilters } from "./api"

type Draft = {
  days: 1 | 7 | 30
  accountId: string
  sourceIp: string
  statusCode: string
}

const initialDraft: Draft = { days: 1, accountId: "", sourceIp: "", statusCode: "" }
const initialApplied: Omit<OperationsRequestErrorFilters, "page"> = {
  days: 1, accountId: "", sourceIp: "", statusCode: null,
}

function isPermissionError(error: unknown): boolean {
  return error instanceof ProblemDetailsError && [401, 403].includes(error.problem.status)
}

function ErrorCard({ item }: { item: OperationsRequestError }) {
  return <article className="operationsRequestErrorCard">
    <div className="operationsRequestErrorCardHead">
      <time dateTime={item.occurred_at}>{formatAuditDateTime(item.occurred_at)}</time>
      <span className="operationsRequestErrorStatus">HTTP {item.status_code}</span>
    </div>
    <dl className="operationsRequestErrorFacts">
      <div>
        <dt>账号</dt>
        <dd className="operationsRequestErrorIdentity">
          {item.account_id ? <>
            <strong>{item.account_name?.trim() || "账号已删除／名称不可用"}</strong>
            <code>{item.account_id}</code>
          </> : <strong>未登录／无法确认账号</strong>}
        </dd>
      </div>
      <div><dt>来源 IP</dt><dd><code>{item.source_ip || "未知来源"}</code></dd></div>
      <div><dt>接口</dt><dd><code>{item.method} {item.route}</code></dd></div>
      <div><dt>原因</dt><dd>{item.reason}</dd></div>
      <div><dt>请求 ID（辅助排查）</dt><dd><code>{item.request_id || "未记录"}</code></dd></div>
    </dl>
  </article>
}

export function OperationsRequestErrorsPanel() {
  const auth = useOptionalAuth()
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<Draft>(initialDraft)
  const [applied, setApplied] = useState(initialApplied)
  const [page, setPage] = useState(1)
  const [formError, setFormError] = useState("")
  const [permissionRevoked, setPermissionRevoked] = useState(false)
  const canView = auth !== null && (
    auth.status === "authenticated" &&
    auth.user?.is_super_admin === true &&
    auth.user.permissions.includes("operations.monitor.read")
  )
  const filters: OperationsRequestErrorFilters = { ...applied, page }
  const errors = useQuery({
    queryKey: operationsKeys.requestErrors(filters),
    queryFn: () => getOperationsRequestErrors(filters),
    enabled: canView && !permissionRevoked,
    retry: (attempt, error) => !isPermissionError(error) && attempt < 1,
  })

  useEffect(() => {
    if (canView && !isPermissionError(errors.error)) return
    if (isPermissionError(errors.error)) setPermissionRevoked(true)
    const key = operationsKeys.requestErrorsRoot()
    const cancellation = queryClient.cancelQueries({ queryKey: key })
    queryClient.removeQueries({ queryKey: key })
    void cancellation.finally(() => queryClient.removeQueries({ queryKey: key }))
  }, [canView, errors.error, queryClient])

  if (!canView) return null

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const statusCode = draft.statusCode === "" ? null : Number(draft.statusCode)
    if (statusCode !== null && (!Number.isInteger(statusCode) || statusCode < 400 || statusCode > 599)) {
      setFormError("HTTP 状态码须为 400 至 599。")
      return
    }
    setFormError("")
    const next = {
      days: draft.days,
      accountId: draft.accountId.trim(),
      sourceIp: draft.sourceIp.trim(),
      statusCode,
    }
    if (page === 1 && Object.entries(next).every(([key, value]) => value === applied[key as keyof typeof applied])) {
      void errors.refetch()
      return
    }
    setPage(1)
    setApplied(next)
  }

  function reset() {
    setDraft(initialDraft)
    setFormError("")
    if (page === 1 && Object.entries(initialApplied).every(([key, value]) => value === applied[key as keyof typeof applied])) {
      void errors.refetch()
      return
    }
    setPage(1)
    setApplied(initialApplied)
  }

  const denied = permissionRevoked || isPermissionError(errors.error)
  const totalPages = errors.data ? Math.max(1, Math.ceil(errors.data.total / errors.data.page_size)) : 1

  return <section className="operationsPanel operationsRequestErrorsPanel" aria-label="请求错误明细">
    <div className="operationsPanelHeading"><h2>请求错误明细</h2><span>HTTP 4xx–5xx</span></div>
    <p className="operationsHelp">仅记录功能启用后的错误，最多保留 30 天；后台批量写入可能短暂延迟。</p>
    {denied ? <p className="formError" role="alert">无权读取请求错误明细，请重新登录或联系管理员。</p> : <>
      <form className="operationsRequestErrorFilters" aria-label="错误筛选" onSubmit={submit}>
        <label>错误范围
          <select value={draft.days} onChange={(event) => setDraft((value) => ({ ...value, days: Number(event.target.value) as Draft["days"] }))}>
            <option value={1}>近 1 天</option><option value={7}>近 7 天</option><option value={30}>近 30 天</option>
          </select>
        </label>
        <label>账号 UUID
          <input type="text" value={draft.accountId} onChange={(event) => setDraft((value) => ({ ...value, accountId: event.target.value }))} placeholder="输入完整账号 UUID" autoComplete="off" />
        </label>
        <label>来源 IP
          <input type="text" value={draft.sourceIp} onChange={(event) => setDraft((value) => ({ ...value, sourceIp: event.target.value }))} placeholder="IPv4 或 IPv6" autoComplete="off" />
        </label>
        <label>HTTP 状态码
          <input type="number" min={400} max={599} step={1} value={draft.statusCode} onChange={(event) => setDraft((value) => ({ ...value, statusCode: event.target.value }))} placeholder="400–599" />
        </label>
        <div className="operationsRequestErrorActions">
          <button type="submit">查询错误</button>
          <button type="button" onClick={reset}>重置筛选</button>
        </div>
      </form>
      {formError ? <p className="formError" role="alert">{formError}</p> : null}
      {errors.isPending ? <p className="operationsRequestErrorState" role="status">正在读取错误记录…</p> : errors.isError ? (
        <p className="formError" role="alert">错误记录读取失败，请检查筛选条件或稍后重试。 <button type="button" onClick={() => void errors.refetch()}>重新读取</button></p>
      ) : <>
        {errors.data.items.length === 0 ? <p className="operationsRequestErrorState">该筛选下暂无错误记录</p> : (
          <div className="operationsRequestErrorList">{errors.data.items.map((item) => <ErrorCard key={item.id} item={item} />)}</div>
        )}
        <div className="operationsPager">
          <span>共 {errors.data.total} 条 · 第 {errors.data.page} / {totalPages} 页</span>
          <button type="button" disabled={page <= 1 || errors.isFetching} onClick={() => setPage((value) => value - 1)}>上一页</button>
          <button type="button" disabled={page >= totalPages || errors.isFetching} onClick={() => setPage((value) => value + 1)}>下一页</button>
        </div>
      </>}
    </>}
  </section>
}
