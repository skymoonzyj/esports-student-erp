import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { Link } from "react-router-dom"
import { ProblemDetailsError } from "../api/client"
import { useOptionalAuth } from "../auth/AuthProvider"
import { assistedPeopleApi, isPrivateDenial } from "./api"
import type { ActivationRolePlan } from "./api"
import { ActivationRolePicker, unavailableActivationRoles } from "./ActivationRolePicker"
import { assistedPeopleKeys } from "./keys"
import { assistedQueryOptions } from "./useAssistedPeopleContext"
import { TeamDialog } from "../teams/TeamDialog"
import "../teams/teams.css"
import "./assisted-people.css"

export function ActivationRolesPanel({ personId, available }: { personId: string; available: boolean }) {
  const auth = useOptionalAuth()
  if (available !== true || auth?.status !== "authenticated" || !auth.user) return null
  const scope = JSON.stringify([auth.user.account_id, auth.user.is_super_admin, auth.user.permissions, auth.user.scoped_permissions])
  return <PlanAccount key={`${scope}:${personId}`} accountId={auth.user.account_id} personId={personId} />
}

function PlanAccount({ accountId, personId }: { accountId: string; personId: string }) {
  const client = useQueryClient()
  const [revoked, setRevoked] = useState(false)
  const [draft, setDraft] = useState<{ version: number; keys: string[] } | null>(null)
  const [pending, setPending] = useState(false)
  const [message, setMessage] = useState("")
  const [confirmation, setConfirmation] = useState<{ version: number; keys: string[] } | null>(null)
  const request = useRef<AbortController | null>(null)
  const attempt = useRef<{ fingerprint: string; key: string } | null>(null)
  useEffect(() => () => { request.current?.abort() }, [])
  const key = assistedPeopleKeys.activationRoles(accountId, personId)
  const query = useQuery({ queryKey: key, queryFn: ({ signal }) => assistedPeopleApi.activationRoles(personId, signal), enabled: !revoked, refetchOnWindowFocus: "always", ...assistedQueryOptions })
  const view = query.isError ? undefined : query.data
  const editable = Boolean(view?.can_edit && !view.account_activated)
  useEffect(() => {
    // Explicit loss of editing authority clears unsaved approvals. A network
    // failure (no validated view) is not evidence of revocation.
    if (view && (!editable || confirmation && confirmation.version !== view.version)) { setDraft(null); attempt.current = null; setConfirmation(null) }
  }, [editable, view, confirmation])
  const options = useQuery({ queryKey: assistedPeopleKeys.activationRoleOptions(accountId), queryFn: ({ signal }) => assistedPeopleApi.activationRoleOptions(signal), enabled: editable && !revoked, refetchOnWindowFocus: "always", ...assistedQueryOptions })
  const denied = isPrivateDenial(query.error) || editable && isPrivateDenial(options.error)
  useEffect(() => {
    if (!denied) return
    request.current?.abort(); attempt.current = null; setDraft(null); setConfirmation(null); setRevoked(true)
    for (const queryKey of [assistedPeopleKeys.activationRoles(accountId, personId), assistedPeopleKeys.activationRoleOptions(accountId)]) {
      void client.cancelQueries({ queryKey }); client.removeQueries({ queryKey })
    }
  }, [accountId, client, denied, personId])
  const selected = draft && draft.version === view?.version ? draft.keys : view?.role_keys ?? []
  const blocked = pending || revoked || query.isFetching || query.isError || !editable || options.isError || options.isFetching || !options.data || Boolean(options.data && unavailableActivationRoles(options.data, selected).length)
  async function save(confirmEndStaffPositions = false) {
    if (blocked || !view || request.current) return
    if (confirmEndStaffPositions && (!confirmation || confirmation.version !== view.version)) return
    const input = confirmEndStaffPositions && confirmation
      ? { role_keys: [...confirmation.keys], expected_version: confirmation.version, confirm_end_staff_positions: true }
      : { role_keys: [...selected].sort(), expected_version: view.version }
    const fingerprint = JSON.stringify(input)
    if (attempt.current?.fingerprint !== fingerprint) attempt.current = { fingerprint, key: crypto.randomUUID() }
    const controller = new AbortController(); request.current = controller
    // This revision also changes for same-version GETs (for example, edit
    // authority revoked without a role-plan change), unlike the plan version.
    const readRevision = client.getQueryState<ActivationRolePlan>(key)?.dataUpdateCount
    setPending(true); setMessage("")
    try {
      const result = await assistedPeopleApi.saveActivationRoles(personId, { ...input, idempotency_key: attempt.current.key }, controller.signal)
      if (controller.signal.aborted || request.current !== controller) return
      const current = client.getQueryState<ActivationRolePlan>(key)
      if (!current || current.status !== "success" || current.fetchStatus !== "idle" || current.isInvalidated ||
        current.dataUpdateCount !== readRevision || current.data?.version !== input.expected_version ||
        !current.data.can_edit || current.data.account_activated || result.version <= input.expected_version) {
        // A concurrent read is authoritative. Never replace its newer version
        // or permissions with an older successful PUT, including same-account
        // responses. Re-read after the uncertain/superseded save completes.
        attempt.current = null; setDraft(null); setConfirmation(null)
        setMessage("保存期间角色计划或权限已重新核实，旧保存结果未采用；请核对重新读取的最新计划后再确认。")
        if (!isPrivateDenial(current?.error)) await query.refetch()
        return
      }
      attempt.current = null; setDraft(null); setConfirmation(null)
      client.setQueryData(key, result)
      setMessage("角色计划已保存，激活后生效；本人仍须完成资料与邮箱验证，激活时会再次复核授权。")
    } catch (error) {
      if (controller.signal.aborted) return
      if (isPrivateDenial(error)) {
        attempt.current = null; setDraft(null); setConfirmation(null); setRevoked(true)
        for (const queryKey of [key, assistedPeopleKeys.activationRoleOptions(accountId)]) {
          void client.cancelQueries({ queryKey }); client.removeQueries({ queryKey })
        }
      } else if (error instanceof ProblemDetailsError && error.problem.status === 409 && error.problem.code === "activation_roles_staff_positions_confirmation_required" && !confirmEndStaffPositions) {
        attempt.current = null
        setConfirmation({ version: input.expected_version, keys: [...input.role_keys] })
      } else if (error instanceof ProblemDetailsError && error.problem.status === 409) {
        attempt.current = null; setDraft(null); setConfirmation(null)
        setMessage("角色计划版本已变化，未保存；已刷新最新计划，请重新核对后显式确认。")
        await query.refetch()
      } else setMessage("角色计划未确认保存成功，草稿已保留；请重新核实权限与目录后重试。")
    } finally {
      if (request.current === controller) {
        request.current = null
        if (!controller.signal.aborted) setPending(false)
      }
    }
  }
  return <section className="personFormCard assistedPeoplePage" aria-label="激活角色计划">
    <h2>激活角色</h2>
    {revoked || denied ? <p role="alert">当前无权查看或修改激活角色计划。</p> : query.isError ? <><p role="alert">暂时无法读取激活角色计划，不能保存。</p><button type="button" onClick={() => void query.refetch()}>重新读取角色计划</button></> : !view ? <p role="status">正在读取激活角色计划…</p> : <>
      <p>额外激活角色：{view.role_names.join("、") || "未设置，保留原最低本人角色规则"}（版本 {view.version}）</p>
      {view.account_activated ? <><p>账号已激活；请通过已有权限管理维护账号角色，本入口不会修改已激活账号。</p><Link to="/settings/permissions">前往权限管理</Link></> : <>
        <p>仅保存激活角色计划，不创建可登录账号、不免除资料、邮箱验证或审核要求；激活后生效。</p>
        {editable ? <>
          {options.isError ? <><p role="alert">暂时无法核实激活角色目录，不能保存。</p><button type="button" onClick={() => void options.refetch()}>重新核实激活角色</button></> : options.data ? <ActivationRolePicker options={options.data} selected={selected} disabled={pending || query.isFetching || options.isFetching || Boolean(confirmation)} onChange={keys => { setDraft({ version: view.version, keys }); setMessage("") }} /> : <p role="status">正在核实激活角色目录…</p>}
          <button type="button" className="primaryButton" disabled={blocked || Boolean(confirmation)} onClick={() => void save()}>保存并重新确认激活角色</button>
        </> : <p>当前仅可查看，修改或重新确认请联系有权管理员。</p>}
      </>}
    </>}
    {message && !revoked && !denied && !confirmation ? <p role="status">{message}</p> : null}
    {confirmation && !revoked && !denied ? <TeamDialog title="确认结束队内岗位" pending={pending} onClose={() => { setConfirmation(null); attempt.current = null; setMessage("") }}>
      <h2>确认结束队内岗位</h2>
      <p>移除的工作人员角色仍有关联队内岗位。确认后，服务端将在同一事务中移除所选角色并结束对应岗位，其他队伍的其他岗位不会因本次操作被额外移除；历史记录保留。重新授予角色不会自动恢复已结束岗位。</p>
      <p>将移除：{view?.role_keys.filter(role => !confirmation.keys.includes(role)).map(role => options.data?.items.find(item => item.key === role)?.name ?? role).join("、") || "请重新核对角色计划"}。保留的额外角色：{confirmation.keys.map(role => options.data?.items.find(item => item.key === role)?.name ?? role).join("、") || "无"}。</p>
      {message ? <p role="status">{message}</p> : null}
      <footer className="teamDialogActions"><button type="button" disabled={pending} onClick={() => { setConfirmation(null); attempt.current = null; setMessage("") }}>取消移除角色</button><button type="button" className="teamPrimaryButton" disabled={blocked} onClick={() => void save(true)}>确认移除角色并结束岗位</button></footer>
    </TeamDialog> : null}
  </section>
}
