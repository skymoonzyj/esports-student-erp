import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { useAuth } from "../auth/AuthProvider"
import { todayDateInput } from "../shared/date"
import { AssistedPersonForm } from "./AssistedPersonForm"
import { isAssistedAdult } from "./calendarAge"
import { assistedPeopleApi } from "./api"
import type { AssistedContext, AssistedRequestFilters, IntakeDetail } from "./api"
import { assistedPeopleKeys } from "./keys"
import { assistedQueryOptions, useAssistedOperation, useAssistedPeopleContext } from "./useAssistedPeopleContext"

const statusLabels = { pending: "待审核", returned: "已退回", approved: "已通过", withdrawn: "已撤回" }
const kindLabels = { student: "学员", player: "队员", both: "学员兼队员" }

export function MyAssistedRequestsPage() {
  return <AssistedRequestsPage view="mine" />
}

/** Shared view with an explicit server-side audience; never role-switched to direct create. */
export function AssistedRequestsPage({ view }: { view: "mine" | "review" }) {
  const { user, status } = useAuth()
  if (!user || status !== "authenticated") return <p>请先登录。</p>
  return <AccountRequests key={`${user.account_id}:${view}`} accountId={user.account_id} view={view} />
}

function AccountRequests({ accountId, view }: { accountId: string; view: "mine" | "review" }) {
  const query = useAssistedPeopleContext(accountId)
  if (query.isPending) return <p role="status">正在核实建档权限…</p>
  const context = query.context
  const allowed = context && (view === "review" ? context.can_review : context.can_submit)
  if (!allowed) return <section><p role="alert">{view === "review" ? "无建档审核权限。" : "无建档申请权限。"}</p><button onClick={() => void query.refetch()}>重新核实权限</button></section>
  return <RequestWorkspace accountId={accountId} view={view} context={context} />
}

function RequestWorkspace({ accountId, view, context }: { accountId: string; view: "mine" | "review"; context: AssistedContext }) {
  const [filters, setFilters] = useState<AssistedRequestFilters>({ view })
  const [page, setPage] = useState(1)
  const [selection, setSelection] = useState<string | "new" | null>(null)
  const list = useQuery({
    queryKey: assistedPeopleKeys.list(accountId, page, filters),
    queryFn: ({ signal }) => assistedPeopleApi.list(page, signal, filters), ...assistedQueryOptions,
  })
  const data = list.isError ? undefined : list.data
  function filter(next: Partial<AssistedRequestFilters>) { setFilters((current) => ({ ...current, ...next })); setPage(1) }
  return <main className="peoplePage">
    <h1>{view === "review" ? "建档审核" : "我的建档申请"}</h1>
    <p>档案审核不代表监护验证、同意或家庭查看授权。待审核、退回或撤回的资料不能入住或激活。</p>
    <div className="personFormActions">
      <label>状态筛选<select value={filters.status ?? ""} onChange={(event) => filter({ status: event.target.value as AssistedRequestFilters["status"] || undefined })}>
        <option value="">全部</option>{Object.entries(statusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
      <label>身份筛选<select value={filters.kind ?? ""} onChange={(event) => filter({ kind: event.target.value as AssistedRequestFilters["kind"] || undefined })}>
        <option value="">全部</option>{Object.entries(kindLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
      {view === "review" ? <label>提交人姓名筛选<input autoComplete="off" value={filters.submitter_query ?? ""} maxLength={80}
        onChange={(event) => filter({ submitter_query: event.target.value })} /></label> : null}
      {view === "mine" ? <button onClick={() => setSelection("new")}>新建申请</button> : null}
    </div>
    {list.isPending ? <p role="status">正在加载申请…</p> : null}
    {list.isError ? <p role="alert">申请列表暂不可用。<button onClick={() => void list.refetch()}>重试列表</button></p> : null}
    {data ? <>
      <p>共 {data.total} 条</p>
      <table><thead><tr><th>申请</th><th>提交人</th><th>身份</th><th>状态</th><th>版本</th></tr></thead><tbody>
        {data.items.map((item, index) => <tr key={item.id}>
          <td><button onClick={() => setSelection(item.id)}>查看申请 {(page - 1) * 20 + index + 1}</button></td>
          <td>{item.submitted_by.label}</td><td>{kindLabels[item.kind]}</td><td>{statusLabels[item.status]}</td><td>{item.version}</td>
        </tr>)}
      </tbody></table>
      {!data.items.length ? <p>没有符合条件的申请。</p> : null}
      <nav aria-label="申请分页"><button disabled={page === 1} onClick={() => setPage((value) => value - 1)}>上一页</button>
        <span>第 {page} 页</span><button disabled={page * 20 >= data.total} onClick={() => setPage((value) => value + 1)}>下一页</button></nav>
    </> : null}
    {selection ? <section aria-label="建档申请详情"><button onClick={() => setSelection(null)}>关闭详情</button>
      {selection === "new" && view === "mine" ? <NewRequest key="new" accountId={accountId} context={context} onSuccess={() => setSelection(null)} />
        : <RequestDetail key={selection} accountId={accountId} requestId={selection} context={context} view={view} />}
    </section> : null}
  </main>
}

function NewRequest({ accountId, context, onSuccess }: { accountId: string; context: AssistedContext; onSuccess: () => void }) {
  const operation = useAssistedOperation(accountId)
  return <><h2>提交建档申请</h2><p>普通申请必须由其他有权管理员审核，不会自动通过。</p>
    {operation.message ? <p role="status">{operation.message}</p> : null}
    {!operation.denied ? <AssistedPersonForm submitting={operation.pending} coachOptions={context.eligible_coaches} teamOptions={context.eligible_teams}
      onSubmit={async (input) => {
        if (await operation.run((signal) => assistedPeopleApi.submit(input, signal))) onSuccess()
        else throw new Error("request_failed")
      }} /> : null}
  </>
}

function RequestDetail({ accountId, requestId, context, view }: { accountId: string; requestId: string; context: AssistedContext; view: "mine" | "review" }) {
  const query = useQuery({ queryKey: assistedPeopleKeys.detail(accountId, requestId),
    queryFn: ({ signal }) => assistedPeopleApi.detail(requestId, signal), ...assistedQueryOptions })
  const client = useQueryClient()
  const [reload, setReload] = useState(0)
  useEffect(() => {
    if (query.isError) client.removeQueries({ queryKey: assistedPeopleKeys.detail(accountId, requestId), exact: true })
  }, [accountId, client, query.isError, requestId])
  // A query may retain old data after refetch failure: never expose it on error.
  const detail = query.isError ? undefined : query.data
  if (query.isError) return <p role="alert">详情无法访问，敏感资料已隐藏。<button onClick={() => {
    client.removeQueries({ queryKey: assistedPeopleKeys.detail(accountId, requestId) })
    void query.refetch()
  }}>重试详情</button></p>
  if (!detail) return <p role="status">正在加载详情…</p>
  return <><button onClick={async () => {
    const result = await query.refetch()
    if (result.isSuccess) setReload((value) => value + 1)
  }}>重新载入最新资料（会替换当前草稿）</button>
    <LoadedDetail key={`${requestId}:${reload}`} accountId={accountId} detail={detail} context={context} view={view} />
  </>
}

function LoadedDetail({ accountId, detail, context, view }: { accountId: string; detail: IntakeDetail; context: AssistedContext; view: "mine" | "review" }) {
  const [reason, setReason] = useState("")
  const [reasonError, setReasonError] = useState(false)
  const operation = useAssistedOperation(accountId)
  const actions = detail.allowed_actions
  const canResubmit = view === "mine" && context.can_submit && detail.status === "returned" && actions.includes("resubmit")
  const canWithdraw = view === "mine" && ["pending", "returned"].includes(detail.status) && actions.includes("withdraw")
  if (operation.denied) return <p role="alert">{operation.message}</p>
  return <>
    <h2>申请详情</h2><p>{statusLabels[detail.status]} · {kindLabels[detail.kind]} · 版本 {detail.version}</p>
    <p>提交人：{detail.submitted_by.label}</p>
    {detail.returned_reason ? <p>退回原因：{detail.returned_reason}</p> : null}
    {operation.message ? <p role="status">{operation.message}</p> : null}
    {detail.scoped_payload ? canResubmit ? <ReturnedRequestEditor detail={detail} context={context} operation={operation} />
      : <AuthorizedFacts detail={detail} context={context} /> : <p>无权查看完整资料。</p>}
    {canWithdraw ? <button disabled={operation.pending} onClick={() => void operation.run((signal) => assistedPeopleApi.withdraw(detail.id, detail.version, signal))}>撤回申请</button> : null}
    {view === "review" && context.can_review ? <>
      {actions.includes("approve") ? <button disabled={operation.pending} onClick={() => void operation.run((signal) => assistedPeopleApi.approve(detail.id, detail.version, signal))}>审核通过</button> : null}
      {actions.includes("return") ? <><label>退回原因<textarea value={reason} maxLength={500} disabled={operation.pending}
        onChange={(event) => { setReason(event.target.value); setReasonError(false) }} /></label>
        {reasonError ? <p role="alert">请填写退回原因。</p> : null}
        <button disabled={operation.pending} onClick={async () => {
          if (!reason.trim()) { setReasonError(true); return }
          if (await operation.run((signal) => assistedPeopleApi.returnRequest(detail.id, detail.version, reason, signal))) setReason("")
        }}>退回修改</button></> : null}
    </> : null}
  </>
}

function ReturnedRequestEditor({ detail, context, operation }: {
  detail: IntakeDetail; context: AssistedContext; operation: ReturnType<typeof useAssistedOperation>
}) {
  // Capture payload and version together for this editable lifecycle. This child
  // survives authorized focus updates, but unmounts when resubmission ends.
  const [initial] = useState(() => ({ id: detail.id, version: detail.version, payload: detail.scoped_payload! }))
  return <AssistedPersonForm initialValue={initial.payload}
    submitting={operation.pending} coachOptions={context.eligible_coaches} teamOptions={context.eligible_teams}
    onSubmit={async (input) => {
      if (!await operation.run((signal) => assistedPeopleApi.resubmit(initial.id, initial.version, input, signal))) throw new Error("request_failed")
    }} />
}

function AuthorizedFacts({ detail, context }: { detail: IntakeDetail; context: AssistedContext }) {
  const payload = detail.scoped_payload!
  const minor = !isAssistedAdult(payload.date_of_birth, todayDateInput())
  return <section aria-label="授权建档资料"><dl>
    <dt>姓名</dt><dd>{payload.full_name}</dd><dt>身份证</dt><dd>{payload.identity_number}</dd>
    <dt>手机号</dt><dd>{payload.phone}</dd><dt>邮箱</dt><dd>{payload.email}</dd>
    <dt>性别</dt><dd>{payload.sex === "male" ? "男" : "女"}</dd><dt>出生日期</dt><dd>{payload.date_of_birth}</dd>
    <dt>年龄身份</dt><dd>{minor ? "未成年人" : "成年人"}</dd>
    <dt>负责教练</dt><dd>{payload.coach_person_ids.map((id) => context.eligible_coaches.find((option) => option.id === id)?.label ?? "当前不可见教练").join("、") || "无"}</dd>
    <dt>队伍</dt><dd>{context.eligible_teams.find((option) => option.id === payload.team_id)?.label ?? "无或当前不可见队伍"}</dd>
  </dl>{payload.guardians.map((guardian, index) => <section key={index} aria-label={`监护人 ${index + 1}`}><h3>监护人 {index + 1}</h3><dl>
    <dt>姓名</dt><dd>{guardian.full_name}</dd><dt>身份证</dt><dd>{guardian.identity_number}</dd><dt>手机号</dt><dd>{guardian.phone}</dd>
    <dt>邮箱</dt><dd>{guardian.email || "未提供"}</dd><dt>关系</dt><dd>{guardian.relationship}</dd>
    <dt>出生日期</dt><dd>{guardian.date_of_birth}</dd><dt>性别</dt><dd>{guardian.sex === "male" ? "男" : "女"}</dd>
    <dt>主要监护人</dt><dd>{guardian.is_primary ? "是" : "否"}</dd>
  </dl></section>)}</section>
}
