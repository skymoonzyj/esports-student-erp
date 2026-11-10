import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useEffect, useRef, useState } from "react"
import { Navigate } from "react-router-dom"
import { apiFetch, ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import { formatDateOnly } from "../shared/date"
import { documentNumberLabel, documentTypeLabel } from "../assistedPeople/documentFacts"
import { claimAdminApi, claimAdminKeys, claimStatusSchema, useClaimAdminContext } from "./adminApi"
import type { ClaimCompletionView, ClaimDetail, ClaimFilters } from "./adminApi"
import "../registration/registration.css"
import "../registration/registrationProcessing.css"
import "./profileClaims.css"

const labels = { pending: "待审核", rejected: "已拒绝", expired: "已过期", invalidated: "已失效", approved: "已绑定，待开户", revoked: "已撤销", completed: "已完成开户" }
const privateFailure = (error: unknown) => error instanceof ProblemDetailsError && [401, 403, 404].includes(error.problem.status)
const completionReasons: Record<string, string> = {
  completion_missing: "补充资料尚不完整，请通过有权编辑的建档流程补齐后再审核。",
  completion_conflicts: "补充资料与原档案存在冲突，请人工核实；本页面不会自动覆盖原资料。",
  completion_invalid: "补充资料未通过有效性校验，请人工核实后重新申请。",
}

function CompletionRead({ value, previous = false }: { value: ClaimCompletionView | null | undefined; previous?: boolean }) {
  const title = previous ? "已获授权的原档案补充资料" : "申请人提交的补充资料"
  const text = (item: string | null) => item || "未提供"
  return <section className="claimCompletionRead" aria-label={title}><h3>{title}</h3>
    {value ? <>
      <dl className="registrationDetailGrid">
        <div><dt>联系电话</dt><dd>{text(value.phone)}</dd></div><div><dt>性别</dt><dd>{value.sex === "male" ? "男" : value.sex === "female" ? "女" : "未提供"}</dd></div>
        <div><dt>出生日期</dt><dd>{text(value.date_of_birth)}</dd></div><div><dt>签发国家</dt><dd>{text(value.issuing_country)}</dd></div>
      </dl>
      <p>监护资料仅用于人工核对，不代表已授予监护权限。</p>
      {!value.guardians.length ? <p>未提供监护资料</p> : value.guardians.map((guardian, index) => <section key={index} aria-label={`${previous ? "原档案" : "申请"}监护人 ${index + 1}`}>
        <h4>监护人 {index + 1}</h4><dl className="registrationDetailGrid">
          <div><dt>姓名</dt><dd>{guardian.full_name}</dd></div><div><dt>{documentNumberLabel(guardian.document_type)}</dt><dd>{guardian.identity_number}</dd></div>
          <div><dt>联系电话</dt><dd>{guardian.phone}</dd></div><div><dt>邮箱</dt><dd>{text(guardian.email)}</dd></div>
          <div><dt>关系</dt><dd>{guardian.relationship}</dd></div><div><dt>性别</dt><dd>{guardian.sex === "male" ? "男" : "女"}</dd></div>
          <div><dt>出生日期</dt><dd>{guardian.date_of_birth}</dd></div><div><dt>主要监护人</dt><dd>{guardian.is_primary ? "是" : "否"}</dd></div>
        </dl>
      </section>)}
    </> : <p>{previous ? "原档案补充资料不可用，不代表原档案缺少资料。需额外建档读取权限及服务端授权。" : "本申请未提供补充资料，或为旧版申请。"}</p>}
  </section>
}

function operationKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID()
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128
  const value = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("")
  return `${value.slice(0, 8)}-${value.slice(8, 12)}-${value.slice(12, 16)}-${value.slice(16, 20)}-${value.slice(20)}`
}

export function ClaimReviewPage() {
  const { user, status } = useAuth()
  if (status !== "authenticated" || !user) return <p role="status">正在确认登录状态…</p>
  return <AccountClaimReviewPage key={user.account_id} accountId={user.account_id} />
}

function AccountClaimReviewPage({ accountId }: { accountId: string }) {
  const { user, refreshUser } = useAuth()
  const context = useClaimAdminContext()
  const client = useQueryClient()
  const [status, setStatus] = useState<ClaimFilters["status"]>("pending")
  const [page, setPage] = useState(1)
  const [detail, setDetail] = useState<ClaimDetail | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [revokeConfirmed, setRevokeConfirmed] = useState(false)
  const [note, setNote] = useState("")
  const [reason, setReason] = useState("")
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [pending, setPending] = useState(false)
  const [blocked, setBlocked] = useState(false)
  const [revalidating, setRevalidating] = useState(false)
  const [refreshFailed, setRefreshFailed] = useState(false)
  const [conflict, setConflict] = useState(false)
  const [revealOpen, setRevealOpen] = useState(false)
  const [purpose, setPurpose] = useState("")
  const [revealed, setRevealed] = useState<string | null>(null)
  const epoch = useRef(0)
  const active = useRef(true)
  const flight = useRef<AbortController | null>(null)
  const revealFlight = useRef<AbortController | null>(null)
  const revealTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const operation = useRef<{ action: string; key: string } | null>(null)
  const refreshing = useRef(false)
  const canReveal = user?.permissions.includes("people.identity.reveal") === true || user?.scoped_permissions?.includes("people.identity.reveal") === true
  const previousRevealPermission = useRef(canReveal)
  const root = claimAdminKeys.privateRoot(accountId)
  const filters: ClaimFilters = { status, page, page_size: 20 }
  const visible = context.allowed && !revalidating && !refreshFailed && !blocked
  const list = useQuery({ queryKey: visible ? claimAdminKeys.list(accountId, filters) : [...claimAdminKeys.root(accountId), "idle"], queryFn: ({ signal }) => claimAdminApi.list(filters, signal),
    enabled: visible, retry: false, staleTime: 0, gcTime: 0, refetchOnWindowFocus: false,
  })

  const clearPrivate = useCallback(() => {
    epoch.current += 1
    flight.current?.abort(); revealFlight.current?.abort()
    flight.current = null; revealFlight.current = null
    if (revealTimer.current) clearTimeout(revealTimer.current)
    setDetail(null); setRevealed(null); setRevealOpen(false); setPurpose("")
    setConfirmed(false); setRevokeConfirmed(false); setNote(""); setReason(""); setPending(false); setConflict(false)
    operation.current = null
    const key = claimAdminKeys.privateRoot(accountId)
    const oldQueries = new Set(client.getQueryCache().findAll({ queryKey: key }))
    const cancellation = client.cancelQueries({ queryKey: key })
    client.removeQueries({ queryKey: key })
    void cancellation.finally(() => client.removeQueries({ predicate: (query) => oldQueries.has(query) }))
  }, [accountId, client])

  const deny = useCallback(() => {
    clearPrivate(); setBlocked(true); setError("当前无权访问此认领资料，请重新确认权限。")
  }, [clearPrivate])

  useEffect(() => {
    if (privateFailure(list.error) || privateFailure(context.error)) deny()
  }, [context.error, deny, list.error])
  useEffect(() => {
    if (context.enabled && context.isSuccess && !context.isFetching && !context.data.can_enter) deny()
  }, [context.data, context.enabled, context.isFetching, context.isSuccess, deny])
  useEffect(() => {
    if (previousRevealPermission.current && !canReveal) clearPrivate()
    previousRevealPermission.current = canReveal
  }, [canReveal, clearPrivate])
  useEffect(() => {
    active.current = true
    return () => {
      active.current = false; epoch.current += 1
      flight.current?.abort(); revealFlight.current?.abort()
      if (revealTimer.current) clearTimeout(revealTimer.current)
      const key = claimAdminKeys.privateRoot(accountId)
      const oldQueries = new Set(client.getQueryCache().findAll({ queryKey: key }))
      void client.cancelQueries({ queryKey: key }).finally(() => client.removeQueries({ predicate: (query) => oldQueries.has(query) }))
      client.removeQueries({ queryKey: key })
    }
  }, [accountId, client])

  const revalidate = useCallback(async () => {
    if (refreshing.current) return
    refreshing.current = true
    clearPrivate(); setRevalidating(true); setRefreshFailed(false); setError(""); setNotice("")
    const revision = epoch.current
    try {
      await refreshUser({ background: true })
      if (!active.current || revision !== epoch.current) return
      const next = await client.fetchQuery({ queryKey: claimAdminKeys.context(accountId), queryFn: ({ signal }) => claimAdminApi.context(signal), staleTime: 0 })
      if (!active.current || revision !== epoch.current) return
      setBlocked(!next.can_enter)
      if (!next.can_enter) deny()
    } catch {
      if (active.current && revision === epoch.current) { setRefreshFailed(true); setError("暂时无法确认审核权限，私人详情已隐藏，请重试。") }
    } finally {
      refreshing.current = false
      if (active.current) setRevalidating(false)
    }
  }, [accountId, clearPrivate, client, deny, refreshUser])
  const revalidateRef = useRef(revalidate)
  revalidateRef.current = revalidate
  useEffect(() => {
    const focus = () => { void revalidateRef.current() }
    const visibility = () => { if (document.visibilityState === "visible") focus() }
    window.addEventListener("focus", focus); document.addEventListener("visibilitychange", visibility)
    return () => { window.removeEventListener("focus", focus); document.removeEventListener("visibilitychange", visibility) }
  }, [])

  async function open(id: string) {
    if (!visible || pending) return
    clearPrivate(); setError(""); setNotice(""); setPending(true)
    const revision = epoch.current
    try {
      const next = await client.fetchQuery({ queryKey: claimAdminKeys.detail(accountId, id), queryFn: ({ signal }) => claimAdminApi.detail(id, signal), staleTime: 0, gcTime: 0, retry: false })
      if (active.current && revision === epoch.current) setDetail(next)
    } catch (caught) {
      if (!active.current || revision !== epoch.current) return
      if (privateFailure(caught)) deny()
      else setError("暂时无法读取认领详情，请重试。")
    } finally { if (active.current && revision === epoch.current) setPending(false) }
  }

  async function decide(action: "approve" | "reject" | "revoke") {
    if (!visible || !detail || pending || conflict || !detail.allowed_actions.includes(action)) return
    if (action === "approve" && (!detail.can_approve || !confirmed)) return
    if (action !== "approve" && !reason.trim()) return
    if (action === "revoke" && (detail.status !== "approved" || !detail.person_id || !revokeConfirmed)) return
    revealFlight.current?.abort(); revealFlight.current = null
    if (revealTimer.current) clearTimeout(revealTimer.current)
    setRevealed(null); setRevealOpen(false); setPurpose("")
    if (operation.current?.action !== action) operation.current = { action, key: operationKey() }
    const input = { expected_version: detail.version, idempotency_key: operation.current.key }
    const controller = new AbortController(); flight.current = controller
    const revision = epoch.current
    setPending(true); setError(""); setNotice("")
    try {
      const next = action === "approve"
        ? await claimAdminApi.approve(detail.id, { ...input, identity_confirmed: true, note: note.trim() || null }, controller.signal)
        : await claimAdminApi[action](detail.id, { ...input, reason: reason.trim() }, controller.signal)
      if (!active.current || controller.signal.aborted || revision !== epoch.current) return
      setDetail(next); setConfirmed(false); setRevokeConfirmed(false); setNote(""); setReason(""); operation.current = null
      setNotice(action === "approve" ? "认领已批准并绑定邮箱，尚未创建账号。" : action === "reject" ? "认领申请已拒绝。" : "认领绑定已撤销。")
      await Promise.all([
        client.invalidateQueries({ queryKey: root }), client.invalidateQueries({ queryKey: claimAdminKeys.context(accountId) }),
        client.invalidateQueries({ queryKey: ["person", detail.person_id] }), client.invalidateQueries({ queryKey: ["people"] }),
        client.invalidateQueries({ queryKey: ["people-counts"] }),
        ...(detail.person_id ? [client.invalidateQueries({ queryKey: ["assisted-editor", accountId, detail.person_id] })] : []),
      ])
    } catch (caught) {
      if (!active.current || controller.signal.aborted || revision !== epoch.current) return
      if (privateFailure(caught)) deny()
      else if (caught instanceof ProblemDetailsError && caught.problem.status === 409) { setConflict(true); setError("版本或状态已变化，当前输入已保留。请重新读取详情并人工核对后再操作。") }
      else setError("操作未成功，输入已保留；请重试。同次操作使用相同操作标识。")
    } finally { if (active.current && revision === epoch.current) { setPending(false); flight.current = null } }
  }

  async function reveal() {
    if (!visible || !detail?.person_id || !canReveal || !purpose.trim() || revealFlight.current) return
    const controller = new AbortController(); revealFlight.current = controller
    const revision = epoch.current
    setError("")
    try {
      const result = await apiFetch<{ value: string }>(`/people/${encodeURIComponent(detail.person_id)}/sensitive-reveal`, {
        method: "POST", signal: controller.signal, cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ field: "identity_number", purpose: purpose.trim() }),
      })
      if (!active.current || controller.signal.aborted || revision !== epoch.current) return
      if (typeof result.value !== "string") throw new Error("Invalid sensitive response")
      setRevealed(result.value); setRevealOpen(false); setPurpose("")
      if (revealTimer.current) clearTimeout(revealTimer.current)
      revealTimer.current = setTimeout(() => setRevealed(null), 60_000)
    } catch (caught) {
      if (!active.current || controller.signal.aborted || revision !== epoch.current) return
      if (privateFailure(caught)) deny()
      else setError("敏感资料暂时无法查看，请检查用途或稍后重试。")
    } finally { if (revision === epoch.current) revealFlight.current = null }
  }

  if (!context.enabled || context.isSuccess && !context.isFetching && !context.data.can_enter) return <Navigate to={user?.permissions.includes("registration.review") ? "/settings/registrations" : "/"} replace />
  const editable = visible && !pending && !conflict
  const draft = visible ? detail : null
  const resetOperation = () => { operation.current = null }
  return <div className="registrationAdminPage claimReviewPage">
    <header className="registrationAdminHeader"><div><h1>档案认领审核</h1><p>需人工核对本人、档案及邮箱；批准不会立即创建账号。</p></div><button type="button" disabled={revalidating} onClick={() => void revalidate()}>重新确认权限</button></header>
    <label>认领状态<select aria-label="认领状态" value={status ?? ""} onChange={(event) => { clearPrivate(); setStatus(event.target.value ? claimStatusSchema.parse(event.target.value) : undefined); setPage(1) }}>
      <option value="">全部状态</option>{claimStatusSchema.options.map((value) => <option key={value} value={value}>{labels[value]}</option>)}
    </select></label>
    {!visible && !blocked && !refreshFailed ? <p role="status">正在确认认领审核权限…</p> : null}
    {error ? <p role="alert" className="registrationError">{error}</p> : null}
    {context.isError && !privateFailure(context.error) && !error ? <p role="alert">暂时无法确认认领审核权限，私人资料已隐藏，请重新确认权限。</p> : null}
    {notice ? <p role="status">{notice}</p> : null}
    {visible && list.isError && !privateFailure(list.error) ? <p role="alert">暂时无法读取认领队列。<button type="button" onClick={() => void list.refetch()}>重新读取队列</button></p> : null}
    {visible && list.isPending ? <p role="status">正在读取认领队列…</p> : null}
    {visible && list.data ? <section aria-label="认领申请队列" className="registrationAdminList">
      <p>共 {list.data.total} 条 · 待审核 {list.data.pending_count} 条 · 第 {page} 页</p>
      {list.data.items.length === 0 ? <p>当前筛选没有认领申请。</p> : null}
      {list.data.items.map((item) => <article key={item.id}><div><strong>{item.masked_name}</strong><span>{item.masked_email} · {labels[item.status]}</span><small>邮箱验证：{formatDateOnly(item.verified_at)}</small><small>有效期：{formatDateOnly(item.expires_at)}</small></div><button type="button" disabled={pending} onClick={() => void open(item.id)}>查看认领详情</button></article>)}
      <div className="claimReviewPagination"><button type="button" disabled={page === 1 || list.isFetching} onClick={() => { clearPrivate(); setPage((value) => value - 1) }}>上一页</button><button type="button" disabled={page * 20 >= list.data.total || list.isFetching} onClick={() => { clearPrivate(); setPage((value) => value + 1) }}>下一页</button></div>
    </section> : null}
    {draft ? <section className="registrationReviewPanel" aria-label="认领申请详情"><header className="registrationSectionHeading"><h2>人工核对认领资料</h2><button type="button" onClick={clearPrivate}>关闭详情</button></header>
      <p>请通过可信渠道核实申请人确为档案本人，再核对姓名、证件与已验证邮箱。系统匹配不代表已完成人工身份核对。</p>
      <dl className="registrationDetailGrid">
        <div><dt>申请人</dt><dd>{draft.applicant_masked_name}</dd></div><div><dt>申请证件（脱敏）</dt><dd>{draft.applicant_masked_identity}</dd></div><div><dt>申请邮箱</dt><dd>{draft.applicant_email}</dd></div>
        {draft.applicant_document_type ? <div><dt>申请证件类型</dt><dd>{documentTypeLabel(draft.applicant_document_type)}</dd></div> : null}
        {draft.applicant_document_type === "passport" ? <div><dt>申请签发国家</dt><dd>{draft.applicant_issuing_country ?? "未提供，须核对补齐后匹配"}</dd></div> : null}
        <div><dt>邮箱验证日期</dt><dd>{formatDateOnly(draft.verified_at)}</dd></div><div><dt>有效期</dt><dd>{formatDateOnly(draft.expires_at)}</dd></div><div><dt>状态／版本</dt><dd>{labels[draft.status]} · v{draft.version}</dd></div>
        <div><dt>原档案</dt><dd>{draft.profile_masked_name ?? "暂无可审核匹配档案"}</dd></div><div><dt>原档案证件</dt><dd>{canReveal && revealed ? revealed : draft.profile_masked_identity ?? "不可用"}</dd></div>
        <div><dt>原档案邮箱</dt><dd>{draft.profile_masked_email ?? (draft.person_id ? "原档案缺邮箱" : "不可用")}</dd></div><div><dt>同目标待审申请数</dt><dd>{draft.same_target_pending_count}</dd></div>
      </dl>
      <CompletionRead value={draft.applicant_completion} />
      <CompletionRead value={draft.profile_completion} previous />
      {draft.private_match_reason ? <p role="note">冲突提示：{completionReasons[draft.private_match_reason] ?? draft.private_match_reason}</p> : null}
      {!draft.can_approve ? <p>当前资料不满足批准条件，请核对冲突、状态与有效期。</p> : null}
      {canReveal && draft.person_id ? <button type="button" disabled={!editable} onClick={() => { setRevealOpen(true); setPurpose(""); setRevealed(null) }}>查看原档案完整证件号码</button> : null}
      {revealOpen && canReveal ? <form onSubmit={(event) => { event.preventDefault(); void reveal() }}><label>查看用途<input aria-label="查看用途" value={purpose} onChange={(event) => setPurpose(event.target.value)} maxLength={500} /></label><p>用途会被审计，请勿填写完整手机号或证件号；明文最多显示一分钟。</p><button type="submit" disabled={!editable || !purpose.trim()}>确认查看</button><button type="button" onClick={() => setRevealOpen(false)}>取消查看</button></form> : null}
      {draft.allowed_actions.includes("approve") ? <><label><input type="checkbox" checked={confirmed} disabled={!editable} onChange={(event) => { setConfirmed(event.target.checked); resetOperation() }} />已人工核对申请人与原档案为同一人</label><label>批准备注（可选）<textarea aria-label="批准备注（可选）" value={note} maxLength={500} disabled={!editable} onChange={(event) => { setNote(event.target.value); resetOperation() }} /></label><button type="button" disabled={!editable || !draft.can_approve || !confirmed} onClick={() => void decide("approve")}>批准认领</button></> : null}
      {draft.allowed_actions.includes("reject") || draft.allowed_actions.includes("revoke") && draft.status === "approved" && draft.person_id ? <><label>拒绝／撤销原因<textarea aria-label="拒绝／撤销原因" value={reason} maxLength={500} disabled={!editable} onChange={(event) => { setReason(event.target.value); resetOperation() }} /></label><p>拒绝原因可能向申请人公开，勿填写他人隐私。</p></> : null}
      {draft.allowed_actions.includes("reject") ? <button type="button" disabled={!editable || !reason.trim()} onClick={() => void decide("reject")}>拒绝申请</button> : null}
      {draft.allowed_actions.includes("revoke") && draft.status === "approved" && draft.person_id ? <><p>仅已绑定且未开户的认领可撤销，最终状态由服务端确认。原先已预留的同一邮箱撤销后仍可保留为联系方式，但不再代表已验证认领绑定；此次新增的邮箱可能被清除。</p><label><input type="checkbox" checked={revokeConfirmed} disabled={!editable} onChange={(event) => { setRevokeConfirmed(event.target.checked); resetOperation() }} />确认撤销尚未开户的认领绑定</label><button type="button" disabled={!editable || !reason.trim() || !revokeConfirmed} onClick={() => void decide("revoke")}>撤销绑定</button></> : null}
      {conflict ? <button type="button" disabled={pending} onClick={() => void open(draft.id)}>重新读取详情</button> : null}
    </section> : null}
  </div>
}
