import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"

import { ProblemDetailsError } from "../api/client"
import { useOptionalAuth } from "../auth/AuthProvider"
import { getTeamContext, join, leave, listTeams, transfer } from "../teams/api"
import type { PersonTeamProjection, TeamSummary } from "../teams/api"
import { teamKeys } from "../teams/keys"
import { clearDeniedTeamResource, isDeniedResource } from "../teams/resourceDenial"
import { TeamDialog } from "../teams/TeamDialog"
import { newTeamOperationKey } from "../teams/TeamActionDialogs"
import "../teams/teams.css"

type Props = { personId: string; projection: PersonTeamProjection; onSaved?: () => void }

async function allAuthorizedTeams(signal: AbortSignal): Promise<TeamSummary[]> {
  const items: TeamSummary[] = []
  for (let page = 1; ; page += 1) {
    signal.throwIfAborted()
    const response = await listTeams({ page, pageSize: 100 })
    signal.throwIfAborted()
    items.push(...response.items)
    if (page * response.page_size >= response.total || response.items.length === 0) return items
  }
}

export function PersonTeamMembershipPanel(props: Props) {
  const auth = useOptionalAuth()
  const accountId = auth?.user?.account_id ?? ""
  const { projection } = props
  if (projection.visibility === "denied") return <section className="teamSelfPanel" aria-label="队伍归属"><h3>队伍归属</h3><p>无查看队伍归属权限</p></section>
  // Target and actor changes discard the draft and make old mutation callbacks inert.
  return <ScopedTeamMembershipPanel key={`${accountId}:${props.personId}:${projection.current?.membership_id ?? "none"}:${projection.current?.team_id ?? "none"}`} {...props} accountId={accountId} />
}

function ScopedTeamMembershipPanel({ personId, projection, onSaved, accountId }: Props & { accountId: string }) {
  const client = useQueryClient()
  const keys = useMemo(() => teamKeys(accountId), [accountId])
  const optionsKey = useMemo(() => [...keys.root, "person-membership-options"], [keys])
  const active = useRef(false)
  const authorityEpoch = useRef(0)
  const busy = useRef(false)
  const saved = useRef(onSaved)
  const [revoked, setRevoked] = useState(false)
  const [projectId, setProjectId] = useState("")
  const [targetId, setTargetId] = useState("")
  const [reason, setReason] = useState("")
  const [dialog, setDialog] = useState<"leave" | "transfer" | null>(null)
  const [operationKey, setOperationKey] = useState(newTeamOperationKey)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  useEffect(() => {
    active.current = true
    return () => { active.current = false; authorityEpoch.current += 1 }
  }, [])
  useEffect(() => { saved.current = onSaved }, [onSaved])

  const context = useQuery({
    queryKey: keys.context(), queryFn: getTeamContext, enabled: Boolean(accountId) && !revoked,
    retry: false, refetchOnWindowFocus: "always",
  })
  const managementAllowed = Boolean(accountId) && context.data?.can_open_management === true && !context.isError && !revoked
  const teams = useQuery({
    queryKey: optionsKey, queryFn: ({ signal }) => allAuthorizedTeams(signal),
    enabled: managementAllowed, retry: false, gcTime: 0, staleTime: 0,
    refetchOnWindowFocus: "always",
  })
  const removeOptions = useCallback(() => {
    void client.cancelQueries({ queryKey: optionsKey, exact: true })
    client.removeQueries({ queryKey: optionsKey, exact: true })
  }, [client, optionsKey])
  const revoke = useCallback(() => {
    if (!active.current) return
    setRevoked(true)
    setDialog(null)
    setTargetId("")
    setProjectId("")
    setReason("")
    setError("队伍权限已变更，请刷新人员档案后重试。")
    removeOptions()
    void client.invalidateQueries({ queryKey: keys.context() })
  }, [client, keys, removeOptions])
  useEffect(() => {
    if (isDeniedResource(context.error) || isDeniedResource(teams.error)) revoke()
  }, [context.error, teams.error, revoke])
  useEffect(() => {
    if (context.data?.can_open_management !== false || context.isFetching) return
    setDialog(null)
    setTargetId("")
    setProjectId("")
    setReason("")
    removeOptions()
  }, [context.data?.can_open_management, context.isFetching, removeOptions])

  const current = projection.current
  const catalog = managementAllowed && !teams.isError ? teams.data : undefined
  const source = catalog?.find((team) => team.id === current?.team_id)
  const canLeave = source?.allowed_actions.includes("manage") === true
  const sourceTransfer = context.data?.can_transfer === true && source?.allowed_actions.includes("transfer") === true
  const choices = (catalog ?? []).filter((team) => team.is_active && team.allowed_actions.includes(current ? "transfer" : "manage"))
  const canChoose = current ? sourceTransfer && choices.some((team) => team.id !== current.team_id) : choices.length > 0
  const projects = [...new Map(choices.map((team) => [team.project_id, team.project_name])).entries()]
  const target = choices.find((team) => team.id === targetId && team.project_id === projectId)
  const unchanged = Boolean(current && target?.id === current.team_id)
  const canWrite = canChoose && Boolean(target) && !unchanged

  // Revoked/refreshed capabilities invalidate an open confirmation immediately.
  const dialogAllowed = managementAllowed && (dialog === "leave" ? canLeave : dialog === "transfer" && canWrite)
  useEffect(() => { if (dialog && !dialogAllowed) setDialog(null) }, [dialog, dialogAllowed])
  const authorityRevision = JSON.stringify([
    managementAllowed, teams.isError, context.data?.can_transfer,
    catalog?.map((team) => [team.id, team.is_active, [...team.allowed_actions].sort()]).sort((a, b) => String(a[0]).localeCompare(String(b[0]))),
  ])
  // A capability lost and later restored must not revive an old operation's callback.
  useEffect(() => { authorityEpoch.current += 1 }, [authorityRevision])

  function changedDraft(): void {
    setOperationKey(newTeamOperationKey())
    setError(null)
    setSuccess(false)
  }
  function openDialog(action: "leave" | "transfer"): void {
    changedDraft()
    setReason("")
    setDialog(action)
  }
  function closeDialog(): void {
    if (busy.current) return
    setDialog(null)
    changedDraft()
  }

  async function submit(action: "join" | "leave" | "transfer"): Promise<void> {
    if (!active.current || busy.current || !managementAllowed || teams.isError) return
    if (action === "leave" ? !canLeave || !current || dialog !== "leave"
      : action === "transfer" ? !current || dialog !== "transfer" || !canWrite || !reason.trim() || reason.trim().length > 200
        : Boolean(current) || !canWrite) return
    busy.current = true
    setPending(true)
    setError(null)
    setSuccess(false)
    const openingEpoch = authorityEpoch.current
    const isCurrent = () => active.current && openingEpoch === authorityEpoch.current
    try {
      if (action === "leave" && current) await leave(current.membership_id, { idempotency_key: operationKey })
      else if (action === "transfer" && current && target) await transfer(current.membership_id, { target_team_id: target.id, reason: reason.trim(), idempotency_key: operationKey })
      else if (action === "join" && target) await join(target.id, { person_id: personId, idempotency_key: operationKey })
      if (!isCurrent()) return
      setDialog(null)
      setTargetId("")
      setReason("")
      setOperationKey(newTeamOperationKey())
      await Promise.all([
        client.invalidateQueries({ queryKey: keys.root }),
        client.invalidateQueries({ queryKey: ["person"] }),
        client.invalidateQueries({ queryKey: ["people"] }),
        client.invalidateQueries({ queryKey: ["people-counts"] }),
      ])
      if (!isCurrent()) return
      setSuccess(true)
      saved.current?.()
    } catch (failure) {
      if (!isCurrent()) return
      if (isDeniedResource(failure)) {
        for (const teamId of [current?.team_id, target?.id]) {
          if (teamId) clearDeniedTeamResource(client, accountId, teamId)
        }
        revoke()
        return
      }
      // Same draft keeps its UUID for safe retries; editing input mints a new one.
      setError(failure instanceof ProblemDetailsError ? failure.problem.detail : "结果未确认，请核对归属后重试；重试将使用原操作键。")
    } finally {
      busy.current = false
      if (active.current) setPending(false)
    }
  }

  return <section className="teamSelfPanel" aria-label="队伍归属">
    <h3>队伍归属</h3>
    {!revoked ? <p>{current ? `${current.project_name} · ${current.team_name}` : "未分配队伍"}</p> : null}
    <p>队伍归属独立保存，不更改业务身份、项目教练、账号权限或历史。</p>
    {context.isLoading ? <p>正在确认队伍权限…</p> : null}
    {context.isError && !revoked ? <p role="alert">暂时无法确认队伍权限。</p> : null}
    {context.data?.can_open_management === false || !accountId ? <p>当前仅可查看队伍归属</p> : null}
    {managementAllowed && teams.isLoading ? <p>正在读取授权队伍…</p> : null}
    {managementAllowed && teams.isError && !revoked ? <p role="alert">暂时无法读取授权队伍。</p> : null}
    {catalog && !canChoose && !canLeave ? <p>当前仅可查看队伍归属</p> : null}
    {canChoose ? <div className="teamActionRow">
      <label>分部<select value={projectId} disabled={pending} onChange={(event) => { setProjectId(event.target.value); setTargetId(""); changedDraft() }}><option value="">请选择分部</option>{projects.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
      <label>队伍<select value={targetId} disabled={pending || !projectId} onChange={(event) => { setTargetId(event.target.value); changedDraft() }}><option value="">请选择队伍</option>{choices.filter((team) => team.project_id === projectId).map((team) => <option key={team.id} value={team.id}>{team.name}</option>)}</select></label>
      {current ? <button type="button" disabled={pending || !canWrite} onClick={() => openDialog("transfer")}>转队</button>
        : <button type="button" disabled={pending || !canWrite} onClick={() => void submit("join")}>加入队伍</button>}
    </div> : null}
    {unchanged ? <p>当前已在所选队伍，无需保存</p> : null}
    {managementAllowed && canLeave ? <button type="button" disabled={pending} onClick={() => openDialog("leave")}>离队</button> : null}
    {error && !dialog ? <p role="alert">{error}</p> : null}
    {success ? <p role="status">队伍归属已保存</p> : null}
    {dialog && dialogAllowed ? <TeamDialog title={dialog === "leave" ? "确认离队" : "确认转队"} pending={pending} onClose={closeDialog}>
      <h3>{dialog === "leave" ? "确认离队" : "确认转队"}</h3>
      <p>{current?.project_name} · {current?.team_name}{dialog === "transfer" ? ` → ${target?.project_name} · ${target?.name}` : ""}</p>
      {dialog === "leave" ? <p>仅结束队伍归属，不删除账号、学员身份或历史。</p>
        : <label>转队原因<textarea value={reason} maxLength={200} disabled={pending} onChange={(event) => { setReason(event.target.value); changedDraft() }} /></label>}
      {error ? <p role="alert">{error}</p> : null}
      <div className="teamDialogActions"><button type="button" disabled={pending} onClick={closeDialog}>取消</button><button type="button" disabled={pending || (dialog === "transfer" && (!reason.trim() || reason.trim().length > 200))} onClick={() => void submit(dialog)}>{dialog === "leave" ? "确认离队" : "确认转队"}</button></div>
    </TeamDialog> : null}
  </section>
}
