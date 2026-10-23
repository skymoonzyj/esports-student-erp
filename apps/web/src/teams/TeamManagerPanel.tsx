import { useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query"
import { useRef, useState } from "react"

import { ProblemDetailsError } from "../api/client"
import { formatDateOnly } from "../shared/date"
import { assign, listAssignments, managerCandidates, unassign } from "./api"
import type { AssignManagerInput, TeamAction, TeamAssignmentView, TeamCandidate, UnassignManagerInput } from "./api"
import { teamKeys } from "./keys"
import { newTeamOperationKey } from "./TeamActionDialogs"
import { clearDeniedTeamResource, isDeniedResource } from "./resourceDenial"
import { accountStatusLabels } from "./presentation"
import { TeamDialog } from "./TeamDialog"

type Props = {
  accountId: string
  teamId: string
  teamIsActive: boolean
  allowedActions: readonly TeamAction[]
  onChanged: () => void | Promise<void>
  onDenied?: (teamId: string) => void
}

type SubmittedOperation =
  | { mode: "assign"; input: AssignManagerInput }
  | { mode: "unassign"; assignmentId: string; input: UnassignManagerInput }

export function TeamManagerPanel(props: Props) {
  return <ResourceTeamManagerPanel key={`${props.accountId}:${props.teamId}`} {...props} />
}

function ResourceTeamManagerPanel({ accountId, teamId, teamIsActive, allowedActions, onChanged, onDenied }: Props) {
  const client = useQueryClient()
  const [denied, setDenied] = useState(false)
  const permitted = allowedActions.includes("assign_managers")
  const [page, setPage] = useState(1)
  const [dialog, setDialog] = useState<"assign" | TeamAssignmentView | null>(null)
  const [query, setQuery] = useState("")
  const [selected, setSelected] = useState<TeamCandidate | null>(null)
  const [operationKey, setOperationKey] = useState(newTeamOperationKey)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [uncertain, setUncertain] = useState(false)
  const [conflict, setConflict] = useState(false)
  const submitted = useRef<SubmittedOperation | null>(null)
  const keys = teamKeys(accountId)
  const assignments = useQuery({
    queryKey: keys.assignments(teamId, page),
    queryFn: () => listAssignments(teamId, { page, pageSize: 20 }),
    enabled: permitted && !denied,
  })
  const trimmedQuery = query.trim()
  const results = useInfiniteQuery({
    queryKey: keys.managerCandidates(teamId, trimmedQuery, null),
    queryFn: ({ pageParam }) => managerCandidates(teamId, { query: trimmedQuery, cursor: pageParam }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    enabled: permitted && !denied && dialog === "assign" && trimmedQuery.length > 0,
  })

  if (!permitted || denied) return null

  function deny(): void {
    setDialog(null)
    setDenied(true)
    if (onDenied) onDenied(teamId)
    else clearDeniedTeamResource(client, accountId, teamId)
  }

  function openAssign(): void {
    if (!teamIsActive) return
    submitted.current = null
    setDialog("assign"); setQuery(""); setSelected(null); setError(null); setUncertain(false); setConflict(false); setOperationKey(newTeamOperationKey())
  }

  function openRemove(item: TeamAssignmentView): void {
    submitted.current = null
    setDialog(item); setError(null); setUncertain(false); setConflict(false); setOperationKey(newTeamOperationKey())
  }

  async function currentAssignments(): Promise<TeamAssignmentView[]> {
    const items: TeamAssignmentView[] = []
    let nextPage = 1
    while (true) {
      const response = await listAssignments(teamId, { page: nextPage, pageSize: 100 })
      items.push(...response.items)
      if (nextPage * response.page_size >= response.total || response.items.length === 0) return items
      nextPage += 1
    }
  }

  async function submit(): Promise<void> {
    if (pending || conflict || !dialog) return
    const operation: SubmittedOperation | null = submitted.current ?? (
      dialog === "assign"
        ? selected ? { mode: "assign", input: { account_id: selected.account_id, idempotency_key: operationKey } } : null
        : { mode: "unassign", assignmentId: dialog.id, input: { idempotency_key: operationKey } }
    )
    if (!operation) return
    submitted.current = operation
    setPending(true)
    setError(null)
    try {
      if (operation.mode === "assign") await assign(teamId, operation.input)
      else await unassign(operation.assignmentId, operation.input)
      await onChanged()
      setDialog(null)
    } catch (failure) {
      if (isDeniedResource(failure)) { deny(); return }
      if (failure instanceof ProblemDetailsError && failure.problem.status === 409) {
        setUncertain(false)
        setConflict(true)
        setError(failure.problem.detail)
        await onChanged()
      } else {
        try {
          const current = await currentAssignments()
          const done = operation.mode === "assign"
            ? current.some((item) => item.account_id === operation.input.account_id)
            : !current.some((item) => item.id === operation.assignmentId)
          if (done) { await onChanged(); setDialog(null); return }
        } catch (reconciliationFailure) {
          if (isDeniedResource(reconciliationFailure)) { deny(); return }
          // Keep the same operation key for genuine network retry.
        }
        setUncertain(true)
        setError("结果未确认，请核对管理人员名单后用原操作键重试；如需改动，请关闭后重新开始。")
      }
    } finally {
      setPending(false)
    }
  }

  const draftLocked = pending || uncertain || conflict

  return (
    <section aria-label="管理人员分配" className="teamManagerPanel">
      <h3>管理人员</h3>
      {teamIsActive ? <button type="button" onClick={openAssign}>分配管理人员</button> : null}
      {assignments.isLoading ? <p>正在读取管理人员…</p> : null}
      {assignments.isError ? <p role="alert">暂时无法读取管理人员。</p> : null}
      {assignments.data?.items.length === 0 ? <p>暂无管理人员</p> : null}
      <ul className="teamAssignmentList">
        {assignments.data?.items.map((item) => <li key={item.id}>
          <span>{item.full_name}{item.nickname ? ` · ${item.nickname}` : ""} · {formatDateOnly(item.started_on)}</span>
          <span>{accountStatusLabels[item.account_status]}</span>
          <button type="button" aria-label={`取消 ${item.full_name} 的管理分配`} onClick={() => openRemove(item)}>取消分配</button>
        </li>)}
      </ul>
      {assignments.data ? <nav className="teamPager" aria-label="管理分配分页"><button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>上一页</button><span>第 {page} 页，共 {assignments.data.total} 条</span><button type="button" disabled={page * assignments.data.page_size >= assignments.data.total} onClick={() => setPage(page + 1)}>下一页</button></nav> : null}
      {dialog ? <TeamDialog title={dialog === "assign" ? "分配管理人员" : "取消管理分配"} pending={pending} onClose={() => setDialog(null)}>
        <header className="teamDialogHeader"><h2>{dialog === "assign" ? "分配管理人员" : "取消管理分配"}</h2><button type="button" disabled={pending} aria-label="关闭弹窗" onClick={() => setDialog(null)}>×</button></header>
        {dialog === "assign" ? <>
          <label>搜索管理人员<input value={query} disabled={draftLocked} onChange={(event) => { setQuery(event.target.value); setSelected(null); setOperationKey(newTeamOperationKey()); setUncertain(false) }} placeholder="姓名或昵称" /></label>
          {trimmedQuery && results.data?.pages.flatMap((page) => page.items).length === 0 ? <p>没有合格候选，请先授予队伍管理角色。</p> : null}
          <ul className="teamCandidateList">{results.data?.pages.flatMap((item) => item.items).map((item) => <li key={item.account_id}><button type="button" disabled={draftLocked} aria-pressed={selected?.account_id === item.account_id} onClick={() => { setSelected(item); setOperationKey(newTeamOperationKey()); setError(null); setUncertain(false) }}>{item.full_name} · {item.nickname ?? "无昵称"} · {item.age}岁{item.is_minor ? " · 未成年" : ""}</button></li>)}</ul>
          {results.hasNextPage ? <button type="button" disabled={draftLocked || results.isFetchingNextPage} onClick={() => void results.fetchNextPage()}>加载更多候选</button> : null}
        </> : <p>仅结束该账号对本队的管理分配，不删除账号或历史。</p>}
        {error ? <p role="alert" className="teamError">{error}</p> : null}
        <footer className="teamDialogActions"><button type="button" disabled={pending} onClick={() => setDialog(null)}>取消</button><button type="button" className="teamPrimaryButton" disabled={pending || conflict || (dialog === "assign" && !selected)} onClick={() => void submit()}>{uncertain ? "用原操作键重试" : dialog === "assign" ? "确认分配" : "确认取消"}</button></footer>
      </TeamDialog> : null}
    </section>
  )
}
