import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useMemo, useRef, useState } from "react"

import { ProblemDetailsError } from "../api/client"
import { formatDateOnly } from "../shared/date"
import { assignPersonStaff, assignStaff, endPersonStaff, endStaffAssignment, listStaffPositions, personStaffCandidates, reconfirmPersonStaff, staffCandidates } from "./api"
import type { AssignStaffInput, ManagerCandidate, PersonStaffAssignInput, PersonStaffCandidate, PersonStaffMaintenanceInput, PersonStaffRow, TeamAction, TeamPage, TeamStaffRole, UnassignManagerInput } from "./api"
import { teamKeys } from "./keys"
import { staffAccountStatusLabels, staffAuthorizationStatusLabels, teamStaffRoleLabels } from "./presentation"
import { clearDeniedTeamResource } from "./resourceDenial"
import { newTeamOperationKey } from "./TeamActionDialogs"
import { TeamDialog } from "./TeamDialog"
import "./TeamStaffPanel.css"

type Props = {
  accountId: string
  teamId: string
  teamIsActive: boolean
  allowedActions: readonly TeamAction[]
  onChanged: () => void | Promise<void>
  onDenied?: (teamId: string) => void
}
type Operation = { mode: "assign-account"; input: AssignStaffInput } | { mode: "end-account"; assignmentId: string; input: UnassignManagerInput }
  | { mode: "assign-person"; input: PersonStaffAssignInput } | { mode: "end-person" | "reconfirm-person"; assignmentId: string; input: PersonStaffMaintenanceInput }
type Maintenance = { row: PersonStaffRow; action: "end" | "reconfirm" }
type Candidate = { source: "account"; item: ManagerCandidate } | { source: "person"; item: PersonStaffCandidate }
const roles: TeamStaffRole[] = ["manager", "coach", "assistant_coach"]

function incompatibleMaintenance(binding: Maintenance, latest: PersonStaffRow): boolean {
  const original = binding.row
  return latest.source_kind !== original.source_kind || latest.person_position_id !== original.person_position_id ||
    latest.person_id !== original.person_id || latest.staff_role !== original.staff_role || latest.version !== original.version ||
    latest.account_id !== original.account_id || latest.account_assignment_id !== original.account_assignment_id ||
    latest.account_status !== original.account_status || latest.authorization_status !== original.authorization_status ||
    latest.ended_on !== null || !latest.allowed_actions.includes(binding.action) ||
    latest.allowed_actions.length !== original.allowed_actions.length || latest.allowed_actions.some(action => !original.allowed_actions.includes(action))
}

const updatedPositionNotice = "岗位状态已更新，旧确认和重试已作废；请核对最新名单并从允许的岗位动作重新开始。若已提交请求，不能据此判断原操作是否生效，请重新核实服务端状态。"

function deniedMessage(failure: unknown): string | null {
  if (!(failure instanceof ProblemDetailsError)) return null
  if (failure.problem.status === 401) return "登录已失效，请重新登录。"
  if (failure.problem.status === 403) return "队伍权限已变更，已清除工作人员资料。"
  if (failure.problem.status === 404) return "队伍不存在或已不可见，已清除工作人员资料。"
  return null
}

export function TeamStaffPanel(props: Props) {
  const client = useQueryClient()
  const permitted = props.allowedActions.includes("assign_managers")
  const { accountId, teamId } = props
  useEffect(() => () => {
    for (const query of client.getQueryCache().findAll({ queryKey: ["teams", accountId] })) {
      if (query.queryKey[3] !== teamId || !["staff-candidates", "staff-assignments", "staff-positions", "person-staff-candidates"].includes(String(query.queryKey[2]))) continue
      void client.cancelQueries({ queryKey: query.queryKey, exact: true })
      client.removeQueries({ queryKey: query.queryKey, exact: true })
    }
  }, [accountId, client, permitted, teamId])
  return permitted ? <StaffAccessBoundary key={`${accountId}:${teamId}:${props.teamIsActive}`} {...props} /> : null
}

function StaffAccessBoundary(props: Props) {
  const [denied, setDenied] = useState<string | null>(null)
  return denied ? <p role="alert">{denied}</p> : <ResourceTeamStaffPanel {...props} onAccessDenied={setDenied} />
}

function ResourceTeamStaffPanel({ accountId, teamId, teamIsActive, onChanged, onDenied, onAccessDenied }: Props & { onAccessDenied: (message: string) => void }) {
  const client = useQueryClient()
  const keys = useMemo(() => teamKeys(accountId), [accountId])
  const [denied, setDenied] = useState<string | null>(null)
  const [status, setStatus] = useState<"current" | "ended">("current")
  const [page, setPage] = useState(1)
  const [dialog, setDialog] = useState<"assign" | Maintenance | null>(null)
  const [source, setSource] = useState<"account" | "person">("account")
  const [reason, setReason] = useState("")
  const [role, setRole] = useState<TeamStaffRole>("manager")
  const [query, setQuery] = useState("")
  const [selected, setSelected] = useState<Candidate | null>(null)
  const [operationKey, setOperationKey] = useState(newTeamOperationKey)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [uncertain, setUncertain] = useState(false)
  const [conflict, setConflict] = useState(false)
  const [positionNotice, setPositionNotice] = useState<string | null>(null)
  const submitted = useRef<Operation | null>(null)
  const request = useRef<AbortController | null>(null)
  const maintenance = useRef<Maintenance | null>(null)
  useEffect(() => () => { request.current?.abort() }, [])
  useEffect(() => client.getQueryCache().subscribe(event => {
    // Only successful, validated roster results are authority. A missing row
    // on another page/filter, pending fetch, or read error proves no revocation.
    if (event.type !== "updated" || event.action.type !== "success") return
    const queryKey = event.query.queryKey
    if (queryKey[0] !== "teams" || queryKey[1] !== accountId || queryKey[2] !== "staff-positions" || queryKey[3] !== teamId) return
    const binding = maintenance.current
    if (!binding) return
    const roster = client.getQueryData<TeamPage<PersonStaffRow>>(queryKey)
    const latest = roster?.items.find(row => row.row_key === binding.row.row_key)
    if (!latest || !incompatibleMaintenance(binding, latest)) return
    // Revoke synchronously at the cache boundary, before a queued UI render or
    // late mutation callback can still submit/replay the captured confirmation.
    maintenance.current = null
    request.current?.abort(); request.current = null; submitted.current = null
    setDialog(null); setReason(""); setSelected(null); setQuery("")
    setPending(false); setUncertain(false); setConflict(false); setError(null)
    setPositionNotice(updatedPositionNotice)
  }), [accountId, client, teamId])
  const assignments = useQuery({
    queryKey: keys.staffPositions(teamId, status, page),
    queryFn: ({ signal }) => listStaffPositions(teamId, { page, pageSize: 20, status }, signal),
    enabled: !denied,
  })
  const trimmedQuery = query.trim()
  const candidates = useInfiniteQuery({
    queryKey: keys.staffCandidates(teamId, role, trimmedQuery),
    queryFn: ({ pageParam }) => staffCandidates(teamId, { staffRole: role, query: trimmedQuery, cursor: pageParam, pageSize: 20 }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    enabled: !denied && dialog === "assign" && source === "account" && Boolean(trimmedQuery) && trimmedQuery.length <= 120,
  })
  const pendingCandidates = useInfiniteQuery({
    queryKey: keys.personStaffCandidates(teamId, role, trimmedQuery),
    queryFn: ({ pageParam, signal }) => personStaffCandidates(teamId, { staffRole: role, query: trimmedQuery, cursor: pageParam, pageSize: 20 }, signal),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    enabled: !denied && dialog === "assign" && source === "person" && Boolean(trimmedQuery) && trimmedQuery.length <= 120,
  })

  useEffect(() => {
    const message = deniedMessage(assignments.error) ?? deniedMessage(candidates.error) ?? deniedMessage(pendingCandidates.error)
    if (!message) return
    setDialog(null)
    setDenied(message)
    onAccessDenied(message)
    clearDeniedTeamResource(client, accountId, teamId)
    onDenied?.(teamId)
  }, [accountId, assignments.error, candidates.error, pendingCandidates.error, client, onAccessDenied, onDenied, teamId])

  function deny(failure: unknown): boolean {
    const message = deniedMessage(failure)
    if (!message) return false
    setDialog(null)
    maintenance.current = null
    setSelected(null)
    setQuery("")
    submitted.current = null
    setDenied(message)
    onAccessDenied(message)
    clearDeniedTeamResource(client, accountId, teamId)
    onDenied?.(teamId)
    return true
  }

  function open(value: "assign" | Maintenance): void {
    if (value !== "assign") {
      const current = client.getQueryState<TeamPage<PersonStaffRow>>(keys.staffPositions(teamId, status, page))
      const latest = current?.status === "success" ? current.data?.items.find(row => row.row_key === value.row.row_key) : undefined
      if (latest && incompatibleMaintenance(value, latest)) { setPositionNotice(updatedPositionNotice); return }
    }
    maintenance.current = value === "assign" ? null : value
    submitted.current = null
    setDialog(value); setRole("manager"); setQuery(""); setSelected(null)
    setSource("account"); setReason("")
    setError(null); setUncertain(false); setConflict(false); setOperationKey(newTeamOperationKey())
    setPositionNotice(null)
  }

  function closeDialog(): void {
    maintenance.current = null
    submitted.current = null
    setDialog(null)
  }

  function draftOperation(): Operation | null {
    if (dialog === "assign") {
      if (!selected) return null
      return selected.source === "person"
        ? { mode: "assign-person", input: { person_id: selected.item.person_id, staff_role: role, idempotency_key: operationKey } }
        : { mode: "assign-account", input: { account_id: selected.item.account_id, staff_role: role, idempotency_key: operationKey } }
    }
    if (!dialog || maintenance.current !== dialog || !dialog.row.allowed_actions.includes(dialog.action)) return null
    const { row, action } = dialog
    if (row.source_kind === "account") return action === "end" && row.account_assignment_id
      ? { mode: "end-account", assignmentId: row.account_assignment_id, input: { idempotency_key: operationKey } } : null
    if (!row.person_position_id || !row.version || !reason.trim()) return null
    return { mode: action === "end" ? "end-person" : "reconfirm-person", assignmentId: row.person_position_id,
      input: { idempotency_key: operationKey, expected_version: row.version, reason: reason.trim() } }
  }

  async function submit(): Promise<void> {
    if (pending || conflict || !dialog || request.current) return
    if (dialog !== "assign" && maintenance.current !== dialog) return
    const operation = submitted.current ?? draftOperation()
    if (!operation) return
    submitted.current = operation
    const controller = new AbortController(); request.current = controller
    let acknowledged = false
    setPending(true); setError(null)
    try {
      if (operation.mode === "assign-account") await assignStaff(teamId, operation.input)
      else if (operation.mode === "end-account") await endStaffAssignment(operation.assignmentId, operation.input)
      else if (operation.mode === "assign-person") await assignPersonStaff(teamId, operation.input, controller.signal)
      else if (operation.mode === "end-person") await endPersonStaff(operation.assignmentId, operation.input, controller.signal)
      else await reconfirmPersonStaff(operation.assignmentId, operation.input, controller.signal)
      if (controller.signal.aborted) return
      // The validated mutation response is authoritative. Retire its old
      // confirmation before the expected refresh advances that row's version.
      // Keep the controller armed through refresh for actor/team/unmount fences.
      acknowledged = true
      closeDialog()
      await onChanged()
      if (controller.signal.aborted) return
      const refreshed = await assignments.refetch()
      if (controller.signal.aborted) return
      if (refreshed.isError) throw refreshed.error
    } catch (failure) {
      if (controller.signal.aborted) return
      if (deny(failure)) return
      if (acknowledged) {
        setPositionNotice("岗位操作已保存，但名单刷新失败；请刷新名单核实最新状态，勿重复提交。")
      } else if (failure instanceof ProblemDetailsError && failure.problem.status === 409) {
        setConflict(true)
        setError(`版本或并发状态已变化：${failure.problem.detail} 请关闭后核对名单，再重新操作。`)
        await onChanged()
      } else if (failure instanceof ProblemDetailsError && failure.problem.status < 500) {
        setConflict(true)
        setError(`绑定操作未完成：${failure.problem.detail} 请关闭后重新核对。`)
      } else {
        setUncertain(true)
        setError("结果未确认，请核对队内岗位后用原操作键重试；如需改动，请关闭后重新开始。")
      }
    } finally { if (request.current === controller) request.current = null; if (!controller.signal.aborted) setPending(false) }
  }

  if (denied) return <p role="alert">{denied}</p>
  const draftLocked = pending || uncertain || conflict
  const activeCandidates = source === "person" ? pendingCandidates : candidates
  const candidateItems: Candidate[] | undefined = activeCandidates.isError ? undefined : source === "person"
    ? pendingCandidates.data?.pages.flatMap(page => page.items.map(item => ({ source: "person" as const, item })))
    : candidates.data?.pages.flatMap(page => page.items.map(item => ({ source: "account" as const, item })))
  const safeAssignments = !assignments.isError ? assignments.data : undefined
  const isReconfirm = dialog !== null && dialog !== "assign" && dialog.action === "reconfirm"
  const title = dialog === "assign" ? "绑定队内工作人员" : isReconfirm ? "重新确认队内岗位授权" : "解绑队内岗位"
  return <section aria-label="队内工作人员（只读）" className="teamStaffPanel">
    <header className="teamPageHeader"><div><h3>队内工作人员（只读）</h3><p className="teamMemberMeta">仅查看所负责队伍的人员与授权住宿摘要；不授予名单编辑、入住、调房或退房权限。</p></div>{teamIsActive ? <button type="button" onClick={() => open("assign")}>绑定队内工作人员</button> : null}</header>
    <label className="teamStaffStatus">岗位绑定状态<select value={status} onChange={(event) => { setStatus(event.target.value as typeof status); setPage(1) }}><option value="current">当前绑定</option><option value="ended">已结束</option></select></label>
    {assignments.isLoading ? <p>正在读取队内工作人员…</p> : null}
    {assignments.isError ? <p role="alert">暂时无法读取队内工作人员，请稍后重试。</p> : null}
    {positionNotice ? <p role="status">{positionNotice}</p> : null}
    {safeAssignments?.items.length === 0 ? <p>暂无{status === "current" ? "当前" : "已结束"}队内岗位绑定。</p> : null}
    <p className="teamMemberMeta">未激活人员的队内归属立即列入名单，但不开放登录或队伍读取权限；账号状态与岗位授权分别核实。</p>
    <ul aria-label="队内岗位绑定" className="teamAssignmentList">{safeAssignments?.items.map((item) => <li key={item.row_key}>
      <div><strong>{item.full_name}</strong>{item.nickname ? ` · ${item.nickname}` : ""}<div className="teamMemberMeta">{teamStaffRoleLabels[item.staff_role]} · <span>{staffAccountStatusLabels[item.account_status]}</span> · <span>{staffAuthorizationStatusLabels[item.authorization_status]}</span> · {formatDateOnly(item.started_on)}{item.ended_on ? ` 至 ${formatDateOnly(item.ended_on)}` : ""}</div></div>
      {status === "current" && !item.ended_on ? <div className="teamStaffRowActions">
        {item.allowed_actions.includes("reconfirm") ? <button type="button" onClick={() => open({ row: item, action: "reconfirm" })}>重新确认授权</button> : null}
        {item.allowed_actions.includes("end") ? <button type="button" aria-label={`解绑 ${item.full_name} 的${teamStaffRoleLabels[item.staff_role]}岗位`} onClick={() => open({ row: item, action: "end" })}>解绑岗位</button> : null}
      </div> : null}
    </li>)}</ul>
    {safeAssignments ? <nav aria-label="队内岗位分页" className="teamPager"><button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button><span>第 {page} 页，共 {safeAssignments.total} 条</span><button type="button" disabled={page * safeAssignments.page_size >= safeAssignments.total} onClick={() => setPage(page + 1)}>下一页</button></nav> : null}
    {dialog ? <TeamDialog title={title} pending={pending} onClose={closeDialog}>
      <header className="teamDialogHeader"><h2>{title}</h2><button type="button" aria-label="关闭弹窗" disabled={pending} onClick={closeDialog}>×</button></header>
      {dialog === "assign" ? <>
        <label>队内岗位<select value={role} disabled={draftLocked} onChange={(event) => { setRole(event.target.value as TeamStaffRole); setSelected(null); setError(null); setOperationKey(newTeamOperationKey()) }}>{roles.map((value) => <option key={value} value={value}>{teamStaffRoleLabels[value]}</option>)}</select></label>
        <label>候选来源<select value={source} disabled={draftLocked} onChange={event => { setSource(event.target.value as typeof source); setSelected(null); setError(null); setOperationKey(newTeamOperationKey()) }}><option value="account">已激活账号</option><option value="person">未激活人员</option></select></label>
        <label>搜索工作人员<input placeholder="姓名或昵称" maxLength={120} value={query} disabled={draftLocked} onChange={(event) => { setQuery(event.target.value); setSelected(null); setError(null); setOperationKey(newTeamOperationKey()) }} /></label>
        {activeCandidates.isFetching ? <p>正在搜索该岗位候选…</p> : null}
        {activeCandidates.isError ? <p role="alert">候选读取失败，请重试或重新确认队伍权限。</p> : null}
        {trimmedQuery && candidateItems?.length === 0 ? <p>{source === "person" ? "没有该岗位的未激活候选，请核对已批准的身份与岗位。" : "没有该岗位的合格候选，请先授予对应账号角色。"}</p> : null}
        <ul className="teamCandidateList">{candidateItems?.map(candidate => <li key={`${candidate.source}:${candidate.item.person_id}`}><button type="button" disabled={draftLocked} aria-pressed={selected?.source === candidate.source && selected.item.person_id === candidate.item.person_id} onClick={() => { setSelected(candidate); setOperationKey(newTeamOperationKey()); setError(null) }}>{candidate.item.full_name} · {candidate.item.nickname ?? "无昵称"} · {candidate.source === "person" ? "未激活" : <>{candidate.item.age === null ? "年龄待补" : `${candidate.item.age}岁`}{candidate.item.is_minor === true ? " · 未成年" : ""}</>}</button></li>)}</ul>
        {activeCandidates.hasNextPage ? <button type="button" disabled={draftLocked || activeCandidates.isFetchingNextPage} onClick={() => void activeCandidates.fetchNextPage()}>加载更多候选</button> : null}
        {selected ? <p>将 {selected.item.full_name} 绑定为本队{teamStaffRoleLabels[role]}，{selected.source === "person" ? "立即记录人员归属；激活时复核后才开放本队只读访问。" : "仅授予只读队内岗位。"}</p> : null}
      </> : <>
        <p>{isReconfirm ? "仅重新核实" : "仅结束"} {dialog.row.full_name} 的本队{teamStaffRoleLabels[dialog.row.staff_role]}岗位，不影响该账号的其他岗位、其他队伍或旧名单管理绑定。</p>
        {dialog.row.source_kind === "person" ? <><label>操作原因<textarea maxLength={200} value={reason} disabled={draftLocked} onChange={event => { setReason(event.target.value); setOperationKey(newTeamOperationKey()) }} /></label><p className="teamMemberMeta">请填写 1–200 字操作原因，勿包含证件、联系方式或其他敏感资料。</p></> : null}
      </>}
      {error ? <p role="alert" className="teamError">{error}</p> : null}
      <footer className="teamDialogActions"><button type="button" disabled={pending} onClick={closeDialog}>取消</button><button type="button" className="teamPrimaryButton" disabled={pending || conflict || !draftOperation()} onClick={() => void submit()}>{uncertain ? "用原操作键重试" : dialog === "assign" ? "确认绑定" : isReconfirm ? "确认重新授权" : "确认解绑"}</button></footer>
    </TeamDialog> : null}
  </section>
}
