import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { Link } from "react-router-dom"
import { useOptionalAuth } from "../auth/AuthProvider"
import { assistedPeopleApi, isPrivateDenial } from "./api"
import { assistedQueryOptions } from "./useAssistedPeopleContext"

export function PendingRoleIntentPanel({ personId }: { personId: string }) {
  const auth = useOptionalAuth()
  if (auth?.status !== "authenticated" || !auth.user?.permissions.includes("personnel_intake.review") || !auth.user.permissions.includes("people.read")) return null
  return <Intent key={`${auth.user.account_id}:${personId}:${JSON.stringify(auth.user.permissions)}`} accountId={auth.user.account_id} personId={personId} />
}

function Intent({ accountId, personId }: { accountId: string; personId: string }) {
  const client = useQueryClient()
  const query = useQuery({ queryKey: ["intake-role-intent", accountId, personId], queryFn: ({ signal }) => assistedPeopleApi.roleIntent(personId, signal), ...assistedQueryOptions, refetchOnWindowFocus: "always" })
  const [busy, setBusy] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [message, setMessage] = useState("")
  const attempt = useRef<{ id: string; version: number; key: string } | null>(null)
  const request = useRef<AbortController | null>(null)
  useEffect(() => () => { request.current?.abort() }, [])
  const view = query.isError ? undefined : query.data
  const denied = isPrivateDenial(query.error)
  useEffect(() => { if (denied || view && !view.can_approve) { request.current?.abort(); attempt.current = null; setConfirmed(false) } }, [denied, view])
  if (denied || query.data === null) return null
  async function approve() {
    if (!view?.can_approve || !confirmed || busy || query.isFetching || request.current) return
    if (attempt.current?.id !== view.id || attempt.current.version !== view.version) attempt.current = { id: view.id, version: view.version, key: crypto.randomUUID() }
    const controller = new AbortController(); request.current = controller; setBusy(true); setMessage("")
    try {
      await assistedPeopleApi.approveRoleIntent(personId, { expected_version: view.version, idempotency_key: attempt.current.key }, controller.signal)
      if (controller.signal.aborted) return
      attempt.current = null; setConfirmed(false); setMessage("角色已批准，请核对最新人员权限。")
      await client.invalidateQueries({ queryKey: ["intake-role-intents"] })
      await query.refetch()
      await client.invalidateQueries({ queryKey: ["assisted-people"] })
    } catch (error) {
      if (controller.signal.aborted) return
      setMessage(isPrivateDenial(error) ? "当前已无审批权限。" : "尚未确认审批成功，请核实最新状态后重试。")
      await query.refetch()
    } finally { if (request.current === controller) { request.current = null; setBusy(false) } }
  }
  return <section aria-label="待授权角色" className="assistedFormNotice">
    <h2>账号角色审批</h2>
    {query.isError ? <><p role="alert">暂时无法加载角色审批状态。</p><button onClick={() => void query.refetch()}>重新读取角色审批</button></> : !view ? <p role="status">正在读取角色审批…</p> : <>
      <p>{view.role_names.join("、")}：{view.status === "approved" ? "已批准" : "待超级管理员批准"}</p>
      {view.status === "pending" ? <p>人员档案已生效，基础账号{view.account_activated ? "已激活" : "可激活"}；待批角色及相关队伍访问暂不生效。</p> : null}
      {view.can_approve ? <><label><input type="checkbox" checked={confirmed} disabled={busy || query.isFetching} onChange={event => setConfirmed(event.target.checked)} />确认批准以上角色及有效队伍岗位</label><button disabled={!confirmed || busy || query.isFetching} onClick={() => void approve()}>{busy ? "正在批准…" : "批准账号角色"}</button></> : null}
    </>}
    {message ? <p role="status">{message}</p> : null}
  </section>
}

export function PendingRoleIntentQueue() {
  const auth = useOptionalAuth()
  if (auth?.status !== "authenticated" || !auth.user?.is_super_admin || !auth.user.permissions.includes("permissions.manage")) return null
  return <Queue key={`${auth.user.account_id}:${JSON.stringify(auth.user.permissions)}`} accountId={auth.user.account_id} />
}
function Queue({ accountId }: { accountId: string }) {
  const [page, setPage] = useState(1)
  const query = useQuery({ queryKey: ["intake-role-intents", accountId, page], queryFn: ({ signal }) => assistedPeopleApi.roleIntents(page, signal), ...assistedQueryOptions, refetchOnWindowFocus: "always" })
  if (isPrivateDenial(query.error)) return null
  const data = query.isError ? undefined : query.data
  return <section className="assistedFormNotice" aria-label="待批准账号角色">
    <h2>待批准账号角色</h2><p>以下人员档案已经生效，仅账号角色等待超级管理员批准。</p>
    {query.isError ? <><p role="alert">角色审批列表加载失败。</p><button onClick={() => void query.refetch()}>重新读取待批准角色</button></> : !data ? <p role="status">正在读取待批准角色…</p> : <>
      {data.items.length === 0 ? <p>暂无待批准角色。</p> : <ul>{data.items.map(item => <li key={item.id}><Link to={`/people/${item.person_id}`}>{item.full_name}</Link> — {item.role_names.join("、")}（{item.account_activated ? "基础账号已激活" : "账号未激活"}）</li>)}</ul>}
      <p>共 {data.total} 人，第 {data.page} 页</p><button disabled={page <= 1 || query.isFetching} onClick={() => setPage(value => value - 1)}>上一页</button><button disabled={page * data.page_size >= data.total || query.isFetching} onClick={() => setPage(value => value + 1)}>下一页</button>
    </>}
  </section>
}
