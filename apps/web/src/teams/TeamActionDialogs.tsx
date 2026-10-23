import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query"
import { useRef, useState } from "react"

import { ProblemDetailsError } from "../api/client"
import {
  candidates, join, leave, listMemberships, listTeams, transfer,
} from "./api"
import type { JoinTeamInput, LeaveTeamInput, TeamAction, TeamCandidate, TeamMemberView, TeamSummary, TransferTeamInput } from "./api"
import { teamKeys } from "./keys"
import { clearDeniedTeamResource, isDeniedResource } from "./resourceDenial"
import { TeamDialog } from "./TeamDialog"

export function newTeamOperationKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID()
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = [...bytes].map((part) => part.toString(16).padStart(2, "0")).join("")
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

async function allCurrentMembers(teamId: string): Promise<TeamMemberView[]> {
  const items: TeamMemberView[] = []
  let page = 1
  while (true) {
    const response = await listMemberships(teamId, { status: "current", page, pageSize: 100 })
    items.push(...response.items)
    if (page * response.page_size >= response.total || response.items.length === 0) return items
    page += 1
  }
}

async function allVisibleTeams(): Promise<TeamSummary[]> {
  const items: TeamSummary[] = []
  let page = 1
  while (true) {
    const response = await listTeams({ page, pageSize: 100 })
    items.push(...response.items)
    if (page * response.page_size >= response.total || response.items.length === 0) return items
    page += 1
  }
}

type Props = {
  accountId: string
  team: TeamSummary
  member?: TeamMemberView
  action: "join" | "leave" | "transfer"
  allowedActions: readonly TeamAction[]
  onClose: () => void
  onChanged: () => void | Promise<void>
  onDenied?: (teamId: string) => void
}

type SubmittedOperation =
  | { action: "join"; teamId: string; input: JoinTeamInput }
  | { action: "leave"; membershipId: string; input: LeaveTeamInput }
  | { action: "transfer"; membershipId: string; input: TransferTeamInput }

export function TeamActionDialogs(props: Props) {
  const { accountId, team, member, action, allowedActions, onClose, onChanged } = props
  const client = useQueryClient()
  const allowed = action === "join" ? allowedActions.includes("manage") && team.is_active
    : action === "leave" ? allowedActions.includes("manage")
      : allowedActions.includes("transfer")
  const [query, setQuery] = useState("")
  const [selected, setSelected] = useState<TeamCandidate | null>(null)
  const [targetTeamId, setTargetTeamId] = useState("")
  const [reason, setReason] = useState("")
  const [operationKey, setOperationKey] = useState(newTeamOperationKey)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [uncertain, setUncertain] = useState(false)
  const [conflict, setConflict] = useState(false)
  const submitted = useRef<SubmittedOperation | null>(null)
  const keys = teamKeys(accountId)
  const trimmedQuery = query.trim()

  const candidateResults = useInfiniteQuery({
    queryKey: keys.candidates(team.id, trimmedQuery, null),
    queryFn: ({ pageParam }) => candidates(team.id, { query: trimmedQuery, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    enabled: allowed && action === "join" && trimmedQuery.length > 0,
  })
  const targets = useQuery({
    queryKey: [...keys.root, "transfer-targets"], queryFn: allVisibleTeams,
    enabled: allowed && action === "transfer",
  })

  if (!allowed || ((action === "leave" || action === "transfer") && !member)) return null

  const title = action === "join" ? "添加队员" : action === "leave" ? "移出队员" : "调队"

  function deny(teamId: string): void {
    onClose()
    if (props.onDenied) props.onDenied(teamId)
    else clearDeniedTeamResource(client, accountId, teamId)
  }

  async function reconcile(operation: SubmittedOperation): Promise<boolean> {
    if (operation.action === "join") {
      const current = await allCurrentMembers(operation.teamId)
      return current.some((item) => item.person_id === operation.input.person_id)
    }
    if (operation.action === "leave") {
      const current = await allCurrentMembers(team.id)
      return !current.some((item) => item.id === operation.membershipId)
    }
    if (member && operation.action === "transfer") {
      const current = await allCurrentMembers(operation.input.target_team_id)
      return current.some((item) => item.person_id === member.person_id)
    }
    return false
  }

  function captureOperation(): SubmittedOperation | null {
    if (action === "join" && selected) {
      return { action, teamId: team.id, input: { person_id: selected.person_id, idempotency_key: operationKey } }
    }
    if (action === "leave" && member) {
      return { action, membershipId: member.id, input: { idempotency_key: operationKey } }
    }
    if (action === "transfer" && member && targetTeamId && reason.trim()) {
      return { action, membershipId: member.id, input: { target_team_id: targetTeamId, reason: reason.trim(), idempotency_key: operationKey } }
    }
    return null
  }

  async function submit(): Promise<void> {
    if (pending || conflict) return
    const operation = submitted.current ?? captureOperation()
    if (!operation) return
    submitted.current = operation
    setPending(true)
    setError(null)
    try {
      if (operation.action === "join") await join(operation.teamId, operation.input)
      else if (operation.action === "leave") await leave(operation.membershipId, operation.input)
      else await transfer(operation.membershipId, operation.input)
      await onChanged()
      onClose()
    } catch (failure) {
      if (isDeniedResource(failure)) { deny(team.id); return }
      if (failure instanceof ProblemDetailsError && failure.problem.status === 409) {
        setUncertain(false)
        setConflict(true)
        setError(failure.problem.detail)
        await onChanged()
      } else {
        try {
          if (await reconcile(operation)) {
            await onChanged()
            onClose()
            return
          }
        } catch (reconciliationFailure) {
          if (isDeniedResource(reconciliationFailure)) {
            deny(operation.action === "transfer" ? operation.input.target_team_id : team.id)
            return
          }
          // A failed reconciliation must not mint a new operation key.
        }
        setUncertain(true)
        setError("结果未确认，请核对名单后用原操作键重试；如需改动，请关闭后重新开始。")
      }
    } finally {
      setPending(false)
    }
  }

  const valid = action === "join" ? selected !== null
    : action === "leave" ? true
      : targetTeamId !== "" && reason.trim().length >= 1 && reason.trim().length <= 200
  const draftLocked = pending || uncertain || conflict

  return (
    <TeamDialog title={title} pending={pending} onClose={onClose}>
        <header className="teamDialogHeader"><h2>{title}</h2><button type="button" aria-label="关闭弹窗" disabled={pending} onClick={onClose}>×</button></header>
        {action === "join" ? (
          <>
            <label>搜索队员<input aria-label="搜索队员" value={query} disabled={draftLocked} onChange={(event) => { setQuery(event.target.value); setSelected(null); setUncertain(false); setConflict(false); setOperationKey(newTeamOperationKey()) }} placeholder="姓名或昵称" /></label>
            {trimmedQuery === "" ? <p>输入姓名、昵称或首字母查找有队员角色或已通过审核的队员档案；无需先激活账号。</p> : null}
            {candidateResults.isFetching && !candidateResults.data ? <p>正在搜索…</p> : null}
            {candidateResults.data?.pages.flatMap((page) => page.items).length === 0 ? <p>没有合格候选，请由有权限人员检查队员角色或审核档案及当前队籍。</p> : null}
            <ul className="teamCandidateList">
              {candidateResults.data?.pages.flatMap((page) => page.items).map((candidate) => (
                <li key={candidate.person_id}><button type="button" disabled={draftLocked} aria-label={`选择 ${candidate.full_name} ${candidate.nickname ?? "无昵称"}`} aria-pressed={selected?.person_id === candidate.person_id} onClick={() => { setSelected(candidate); setOperationKey(newTeamOperationKey()); setUncertain(false); setConflict(false); setError(null) }}>
                  <strong>{candidate.full_name}</strong> <span>{candidate.nickname ?? "无昵称"} · {candidate.age}岁{candidate.account_id === null ? " · 未激活" : ""}</span>
                </button></li>
              ))}
            </ul>
            {selected ? <p>已选：{selected.full_name} · {selected.nickname ?? "无昵称"} · {selected.age}岁</p> : null}
            {candidateResults.hasNextPage ? <button type="button" disabled={draftLocked || candidateResults.isFetchingNextPage} onClick={() => void candidateResults.fetchNextPage()}>加载更多候选</button> : null}
          </>
        ) : null}
        {action === "leave" ? <p>仅结束队伍归属，不删除账号、学员身份或历史</p> : null}
        {action === "transfer" ? (
          <>
            <label>目标队伍<select value={targetTeamId} disabled={draftLocked} onChange={(event) => { setTargetTeamId(event.target.value); setOperationKey(newTeamOperationKey()); setUncertain(false); setConflict(false) }}><option value="">请选择目标队伍</option>{targets.data?.filter((item) => item.id !== team.id && item.is_active).map((item) => <option key={item.id} value={item.id}>{item.project_name} · {item.name}</option>)}</select></label>
            <label>调队原因<textarea value={reason} disabled={draftLocked} maxLength={200} onChange={(event) => { setReason(event.target.value); setOperationKey(newTeamOperationKey()); setUncertain(false); setConflict(false) }} /></label>
          </>
        ) : null}
        {error ? <p className="teamError" role="alert">{error}</p> : null}
        <footer className="teamDialogActions">
          <button type="button" disabled={pending} onClick={onClose}>取消</button>
          {uncertain ? <button type="button" className="teamPrimaryButton" disabled={pending} onClick={() => void submit()}>用原操作键重试</button>
            : <button type="button" className="teamPrimaryButton" disabled={pending || conflict || !valid || (action === "join" && candidateResults.isFetching)} onClick={() => void submit()}>{action === "join" ? "确认添加" : action === "leave" ? "确认移出" : "确认调队"}</button>}
        </footer>
    </TeamDialog>
  )
}
