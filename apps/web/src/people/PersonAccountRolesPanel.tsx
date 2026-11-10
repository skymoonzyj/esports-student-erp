import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { ProblemDetailsError } from "../api/client"
import { useOptionalAuth } from "../auth/AuthProvider"
import { assignRole, changeAccountJobRole, getAccountAccess, listRoles, removeRole } from "../settings/api"
import type { AccountJobRoleChangeInput, Role } from "../settings/api"
import "./PersonAccountRolesPanel.css"

type Props = { personId: string; accountId: string | null; canManageAccountRoles: boolean; accountStatus?: string }
const staffRoleKeys = new Set(["admin", "super_admin", "coach", "assistant_coach", "customer_service", "life_teacher", "staff", "administration", "finance", "team_manager", "property"])
type JobConfirmation = { input: AccountJobRoleChangeInput; removed: Role[]; retained: Role[]; added: Role[] }

export function PersonAccountRolesPanel({ personId, accountId, canManageAccountRoles, accountStatus }: Props) {
  const auth = useOptionalAuth()
  if (auth?.status !== "authenticated" || !auth.user) return null
  if (!accountId) return accountStatus === "not_activated" ? <p className="formHint">本档案尚未激活账号；激活账号后，由有权人员独立维护账号权限角色。</p> : null
  if (!canManageAccountRoles || !(auth.user.is_super_admin || auth.user.permissions.includes("permissions.manage"))) return null
  const scope = JSON.stringify([Boolean(auth.user.is_super_admin), [...auth.user.permissions].sort(), [...(auth.user.scoped_permissions ?? [])].sort()])
  return <AccountRoles key={`${auth.user.account_id}:${personId}:${accountId}:${scope}`} actorId={auth.user.account_id} personId={personId} accountId={accountId} scope={scope} />
}

function AccountRoles({ actorId, personId, accountId, scope }: { actorId: string; personId: string; accountId: string; scope: string }) {
  const client = useQueryClient()
  const auth = useOptionalAuth()
  const [roleId, setRoleId] = useState("")
  const [confirmation, setConfirmation] = useState<{ kind: "grant" | "revoke"; role: Role } | null>(null)
  const [fromRoleId, setFromRoleId] = useState("")
  const [toRoleId, setToRoleId] = useState("")
  const [keepConcurrent, setKeepConcurrent] = useState<boolean | null>(null)
  const [jobConfirmation, setJobConfirmation] = useState<JobConfirmation | null>(null)
  const [pending, setPending] = useState(false)
  const [revoked, setRevoked] = useState(false)
  const [message, setMessage] = useState("")
  const [succeeded, setSucceeded] = useState(false)
  const active = useRef(false)
  const inFlight = useRef(false)
  useEffect(() => { active.current = true; return () => { active.current = false } }, [])
  const key = ["person-account-roles", actorId, personId, accountId, scope] as const
  const query = useQuery({ queryKey: key, queryFn: async ({ signal }) => {
    const [catalog, access] = await Promise.all([listRoles(), getAccountAccess(accountId)])
    signal.throwIfAborted()
    if (access.account_id !== accountId) throw new Error("账号权限响应不匹配")
    return { catalog, access }
  }, retry: false, gcTime: 0, staleTime: 0, refetchOnWindowFocus: false, enabled: !revoked })
  const error = query.error
  const lost = revoked || error instanceof ProblemDetailsError && [401, 403, 404].includes(error.problem.status)
  useEffect(() => {
    if (lost) {
      void client.cancelQueries({ queryKey: key })
      client.removeQueries({ queryKey: key })
    }
  }, [lost, client, actorId, personId, accountId, scope])
  function resetJob() {
    setJobConfirmation(null); setFromRoleId(""); setToRoleId(""); setKeepConcurrent(null)
  }
  function editJob() { setJobConfirmation(null); setConfirmation(null); setMessage(""); setSucceeded(false) }
  function previewJob() {
    if (!query.data || pending || query.isFetching || query.isError || lost || !fromRoleId || !toRoleId || keepConcurrent === null || fromRoleId === toRoleId) return
    const current = query.data.access.roles
    const from = current.find(role => role.id === fromRoleId && staffRoleKeys.has(role.key))
    const to = query.data.catalog.find(role => role.id === toRoleId && staffRoleKeys.has(role.key))
    if (!to || (!from && (fromRoleId !== "none" || current.some(role => staffRoleKeys.has(role.key))))) return
    const removed = current.filter(role => role.id !== to.id && staffRoleKeys.has(role.key) && (role.id === from?.id || !keepConcurrent))
    setConfirmation(null); setMessage("")
    setJobConfirmation({ input: { from_role_id: from?.id ?? null, to_role_id: to.id, keep_concurrent_roles: keepConcurrent, expected_role_ids: current.map(role => role.id) }, removed, retained: current.filter(role => !removed.some(item => item.id === role.id)), added: current.some(role => role.id === to.id) ? [] : [to] })
  }
  async function confirmJob() {
    if (!jobConfirmation || pending || inFlight.current || query.isFetching || query.isError || lost) return
    inFlight.current = true
    setPending(true); setMessage(""); setSucceeded(false)
    try {
      const result = await changeAccountJobRole(accountId, jobConfirmation.input)
      if (result.account_id !== accountId) throw new Error("岗位响应不匹配")
      if (!active.current) return
      resetJob(); setRoleId("")
      // The mutation may change the operator's own authority. Refresh it even if
      // the follow-up access read is denied by their new role.
      if (accountId === actorId) void auth?.refreshUser({ background: true }).catch(() => undefined)
      await Promise.all([
        query.refetch(),
        client.invalidateQueries({ queryKey: ["account-access", accountId] }),
        client.invalidateQueries({ queryKey: ["person", personId] }),
        client.invalidateQueries({ predicate: item => item.queryKey[0] === "person-assignments" && item.queryKey[2] === personId }),
        client.invalidateQueries({ queryKey: ["people"] }),
        client.invalidateQueries({ queryKey: ["people-counts"] }),
      ])
      if (!active.current) return
      setSucceeded(true); setMessage("岗位已更新；请以重新读取的账号角色为准。")
    } catch (caught) {
      if (!active.current) return
      resetJob(); setConfirmation(null); setRoleId("")
      if (caught instanceof ProblemDetailsError && [401, 403, 404].includes(caught.problem.status)) {
        setRevoked(true)
        void client.cancelQueries({ queryKey: key }); client.removeQueries({ queryKey: key })
        void auth?.refreshUser({ background: true }).catch(() => undefined)
      } else {
        setMessage("岗位变更未确认成功，已清除原确认，请重新读取并核对当前角色后重新选择。")
        await query.refetch()
      }
    } finally {
      if (active.current) { inFlight.current = false; setPending(false) }
    }
  }
  async function confirm() {
    if (!confirmation || pending || inFlight.current || query.isError || lost) return
    inFlight.current = true
    setPending(true); setMessage(""); setSucceeded(false)
    try {
      if (confirmation.kind === "grant") await assignRole(accountId, confirmation.role.id)
      else await removeRole(accountId, confirmation.role.id)
      if (!active.current) return
      setConfirmation(null); setRoleId(""); resetJob()
      await query.refetch()
      if (!active.current) return
      await client.invalidateQueries({ queryKey: ["account-access", accountId] })
      if (!active.current) return
      setSucceeded(true); setMessage("账号角色已更新")
      if (accountId === actorId) void auth?.refreshUser({ background: true }).catch(() => undefined)
    } catch (caught) {
      if (!active.current) return
      if (caught instanceof ProblemDetailsError && [401, 403, 404].includes(caught.problem.status)) {
        setRevoked(true); setConfirmation(null); setRoleId("")
        void client.cancelQueries({ queryKey: key }); client.removeQueries({ queryKey: key })
        void auth?.refreshUser({ background: true }).catch(() => undefined)
      } else setMessage(caught instanceof ProblemDetailsError ? caught.problem.detail : "暂时无法更新账号角色，请核对后重试。")
    } finally {
      if (active.current) { inFlight.current = false; setPending(false) }
    }
  }
  if (lost) return <p role="alert">账号角色维护权限已失效，请刷新当前权限。</p>
  return <section className="detailCard personAccountRoles" aria-label="账号权限角色"><h2>账号权限角色</h2>
    <p className="formHint">账号角色与学员／队员业务身份分开维护，授予角色会改变登录权限，请明确核对后操作。</p>
    {query.isError ? <p role="alert">暂时无法核实账号角色，操作已暂停。<button type="button" disabled={pending} onClick={() => void query.refetch()}>重新读取账号角色</button></p> : null}
    {!query.data ? <p>正在读取账号角色…</p> : <>
      <ul>{query.data.access.roles.map(role => <li key={role.id}>{role.name} <button className="secondaryButton" type="button" disabled={pending || query.isError || accountId === actorId && role.key === "super_admin"} onClick={() => { resetJob(); setConfirmation({ kind: "revoke", role }); setMessage("") }}>{accountId === actorId && role.key === "super_admin" ? "当前超级管理员角色受保护" : `移除 ${role.name}`}</button></li>)}</ul>
      {!query.data.access.roles.length ? <p>当前没有分配账号角色。</p> : null}
      <label>添加账号角色<select value={roleId} disabled={pending || query.isError} onChange={event => { resetJob(); setRoleId(event.target.value); setConfirmation(null); setMessage("") }}><option value="">请选择角色</option>{query.data.catalog.filter(role => !query.data.access.roles.some(current => current.id === role.id)).map(role => <option key={role.id} value={role.id}>{role.name}</option>)}</select></label>
      <button className="secondaryButton" type="button" disabled={!roleId || pending || query.isError} onClick={() => { const role = query.data.catalog.find(item => item.id === roleId); if (role) { setConfirmation({ kind: "grant", role }); setMessage("") } }}>分配账号角色</button>
      {confirmation ? <div role="dialog" aria-label={confirmation.kind === "grant" ? "确认分配账号角色" : "确认移除账号角色"}>
        <p>确认{confirmation.kind === "grant" ? "分配" : "移除"}账号角色「{confirmation.role.name}」？此操作独立改变账号登录权限，不修改业务身份。</p>
        <button className="primaryButton" type="button" disabled={pending || query.isError} onClick={() => void confirm()}>{confirmation.kind === "grant" ? "确认分配角色" : "确认移除角色"}</button>
        <button className="secondaryButton" type="button" disabled={pending} onClick={() => setConfirmation(null)}>取消角色操作</button>
      </div> : null}
      <h3>岗位变更</h3>
      <p className="formHint">一次确认完成岗位替换。学员、队员、监护人、注册用户等身份角色与业务归属不变；单独 allow/deny 授权保持不变，同名权限可能仍然有效。</p>
      <fieldset disabled={pending || query.isError || query.isFetching}>
        <legend>选择岗位及兼任处理方式</legend>
        <label>原岗位<select value={fromRoleId} onChange={event => { setFromRoleId(event.target.value); editJob() }}>
          <option value="">请选择原岗位</option>
          {!query.data.access.roles.some(role => staffRoleKeys.has(role.key)) ? <option value="none">当前无工作人员岗位</option> : null}
          {query.data.access.roles.filter(role => staffRoleKeys.has(role.key)).map(role => <option key={role.id} value={role.id} disabled={accountId === actorId && role.key === "super_admin"}>{role.name}</option>)}
        </select></label>
        <label>新岗位<select value={toRoleId} onChange={event => { setToRoleId(event.target.value); editJob() }}>
          <option value="">请选择新岗位</option>
          {query.data.catalog.filter(role => staffRoleKeys.has(role.key)).map(role => <option key={role.id} value={role.id} disabled={role.id === fromRoleId}>{role.name}</option>)}
        </select></label>
        <fieldset><legend>是否保留其他兼任岗位？（必须明确选择）</legend>
          <label className="checkboxLabel"><input type="radio" name={`keep-concurrent-${accountId}`} checked={keepConcurrent === true} onChange={() => { setKeepConcurrent(true); editJob() }} />是，保留其他兼任岗位</label>
          <label className="checkboxLabel"><input type="radio" name={`keep-concurrent-${accountId}`} checked={keepConcurrent === false} onChange={() => { setKeepConcurrent(false); editJob() }} />否，撤销其他工作人员岗位</label>
        </fieldset>
        <button className="secondaryButton" type="button" disabled={!fromRoleId || !toRoleId || keepConcurrent === null || fromRoleId === toRoleId} onClick={previewJob}>预览岗位变更</button>
      </fieldset>
      {jobConfirmation ? <div role="dialog" aria-label="确认岗位变更">
        <p>请核对岗位变更；确认后一次提交，不自动激活账号。</p>
        <p>将撤销：{jobConfirmation.removed.map(role => role.name).join("、") || "无"}</p>
        <p>将保留：{jobConfirmation.retained.map(role => role.name).join("、") || "无"}</p>
        <p>将新增：{jobConfirmation.added.map(role => role.name).join("、") || "无（新岗位已持有）"}</p>
        <p>学员／队员等身份、项目与队伍归属不变；单独 allow/deny 授权不变，撤销角色不一定撤销同名单独授权权限。</p>
        <button className="primaryButton" type="button" disabled={pending || query.isError || query.isFetching} onClick={() => void confirmJob()}>确认岗位变更</button>
        <button className="secondaryButton" type="button" disabled={pending} onClick={() => setJobConfirmation(null)}>取消岗位操作</button>
      </div> : null}
    </>}
    {message ? <p role={succeeded ? "status" : "alert"}>{message}</p> : null}
  </section>
}
