import { lazy, Suspense, useEffect, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useAuth } from "../auth/AuthProvider"
import { AssistedPersonForm } from "./AssistedPersonForm"
import { assistedPeopleApi, isPrivateDenial } from "./api"
import type { AssistedContext } from "./api"
import { useAssistedOperation, useAssistedPeopleContext } from "./useAssistedPeopleContext"
import { PendingPersonForm, PendingReadinessSummary } from "./PendingPersonForm"
import type { PendingPersonDraft } from "./PendingPersonForm"
import { assistedEditorApi, newEditorOperationKey } from "./editorApi"
import { identityValidation } from "./IntakeIdentitySection"
import { emptyIdentityDraft, intakePayloadFingerprint, migrateLegacyIdentityDraft, normalizeIdentityInput, selectedIdentities, identityNames, staffRoleIdentity } from "./intakeIdentity"
import type { IntakeIdentityDraft } from "./intakeIdentity"
import type { TeamStaffRole } from "./types"
import { assistedPeopleKeys } from "./keys"
import { assistedQueryOptions } from "./useAssistedPeopleContext"

const QuickImportPanel = lazy(() => import("./QuickImportPanel").then(module => ({ default: module.QuickImportPanel })))

export function AssistedDirectCreatePage() {
  const { user, status } = useAuth()
  const client = useQueryClient()
  const previousAccount = useRef(user?.account_id)
  useEffect(() => {
    const old = previousAccount.current
    previousAccount.current = user?.account_id
    if (old && old !== user?.account_id) client.removeQueries({ queryKey: ["assisted-people", old, "pending-create-draft"] })
  }, [client, user?.account_id])
  if (!user || status !== "authenticated") return <p>请先登录。</p>
  return <DirectAccount key={user.account_id} accountId={user.account_id} />
}

function DirectAccount({ accountId }: { accountId: string }) {
  const query = useAssistedPeopleContext(accountId)
  if (query.isPending) return <p role="status">正在核实管理建档权限…</p>
  if (query.isError) return <><p role="alert">暂时无法核实管理建档权限，请恢复连接后重试；未确认权限前不能提交。</p><button onClick={() => void query.refetch()}>重新核实管理权限</button></>
  if (!query.context?.can_direct_create) return <p role="alert">无管理建档权限。</p>
  return <DirectForm accountId={accountId} context={query.context} revalidating={query.isFetching} />
}

function DirectForm({ accountId, context, revalidating }: { accountId: string; context: AssistedContext; revalidating: boolean }) {
  const [quickImport, setQuickImport] = useState(false)
  const [quickImportLoaded, setQuickImportLoaded] = useState(false)
  const client = useQueryClient()
  const draftKey = ["assisted-people", accountId, "pending-create-draft"] as const
  type SavedDraft = { schemaVersion?: 2; identity?: IntakeIdentityDraft; draft?: PendingPersonDraft; roleKeys?: string[]; attempt?: { fingerprint: string; key: string } }
  const options = useQuery({ queryKey: [...assistedPeopleKeys.account(accountId), "management-identity-context"], queryFn: ({ signal }) => assistedPeopleApi.managementIdentityContext(signal), refetchOnWindowFocus: "always", ...assistedQueryOptions })
  function restorePendingIdentity(saved: SavedDraft | undefined) {
    if (saved?.schemaVersion === 2 && saved.identity) return { value: saved.identity, error: "" }
    try { return { value: saved ? migrateLegacyIdentityDraft(saved).value : emptyIdentityDraft(), error: "" } }
    catch (error) { return { value: emptyIdentityDraft(), error: error instanceof Error ? error.message : "旧草稿无法识别，请明确清除后重新建档" } }
  }
  const [restored] = useState(() => restorePendingIdentity(context.can_direct_create_pending ? client.getQueryData<SavedDraft>(draftKey) : undefined))
  const [identity, setIdentity] = useState(restored.value)
  const strictIdentity = useRef(emptyIdentityDraft())
  const [migrationError, setMigrationError] = useState(restored.error)
  const [mode, setMode] = useState<"strict" | "pending">(() => context.can_direct_create_pending && client.getQueryData<SavedDraft>(draftKey) ? "pending" : "strict")
  const [confirmed, setConfirmed] = useState(false)
  const [completed, setCompleted] = useState(false)
  const [formEpoch, setFormEpoch] = useState(0)
  const [confirmationError, setConfirmationError] = useState(false)
  const [createdPersonId, setCreatedPersonId] = useState<string | null>(null)
  const pendingAttempt = useRef<{ fingerprint: string; key: string } | null>(context.can_direct_create_pending ? client.getQueryData<SavedDraft>(draftKey)?.attempt ?? null : null)
  const strictAttempt = useRef<{ fingerprint: string; key: string } | null>(null)
  const operation = useAssistedOperation(accountId)
  const verifying = options.isError || !options.data || options.isFetching
  const authorityBlocked = Boolean(options.data && identity.primary_identity && Object.keys(identityValidation(identity, options.data)).length)
  const roleBlocked = verifying || authorityBlocked || Boolean(migrationError)
  const blocked = operation.pending || revalidating || roleBlocked
  const roleDenied = options.isError && isPrivateDenial(options.error)
  function changeIdentity(value: IntakeIdentityDraft) {
    setIdentity(value)
    setConfirmed(false)
    if (mode === "strict") strictIdentity.current = value
    if (mode === "pending") client.setQueryData<SavedDraft>(draftKey, current => ({ schemaVersion: 2, draft: current?.draft, attempt: current?.attempt, identity: value }))
  }
  useEffect(() => {
    if (options.isError && isPrivateDenial(options.error)) {
      setIdentity(emptyIdentityDraft()); strictIdentity.current = emptyIdentityDraft(); pendingAttempt.current = null; strictAttempt.current = null
      client.removeQueries({ queryKey: ["assisted-people", accountId, "pending-create-draft"] })
    }
  }, [accountId, client, options.isError, options.error])
  useEffect(() => { if (operation.denied) { strictIdentity.current = emptyIdentityDraft(); pendingAttempt.current = null; strictAttempt.current = null; client.removeQueries({ queryKey: ["assisted-people", accountId, "pending-create-draft"] }) } }, [accountId, client, operation.denied])
  useEffect(() => {
    if (!context.can_direct_create_pending) {
      pendingAttempt.current = null
      if (mode === "pending") { setMode("strict"); setIdentity(emptyIdentityDraft()); setConfirmed(false); setConfirmationError(false) }
    }
  }, [context.can_direct_create_pending, mode])
  const confirmation = <section aria-label="提交管理建档确认" className="intakeSubmitConfirmation">
    <p>身份：{selectedIdentities(identity).map(key => identityNames[key]).join("、") || "尚未选择"}；队员队伍：{identity.player_team_id ? context.eligible_teams.find(item => item.id === identity.player_team_id)?.label ?? "请核实" : "未分配"}；工作人员岗位归属：{Object.values(identity.staff_team_ids).reduce((total, ids) => total + ids.length, 0)} 项。</p>
    {(Object.keys(staffRoleIdentity) as TeamStaffRole[]).filter(role => identity.staff_team_ids[role].length).map(role => <p key={role}>{identityNames[staffRoleIdentity[role]]}：{[...identity.staff_team_ids[role]].sort().map(teamId => { const team = options.data?.staff_team_options.find(item => item.id === teamId); return team ? `${team.project_name} / ${team.name}` : "已失效队伍（请移除）" }).join("、")}</p>)}
    <label><input type="checkbox" checked={confirmed} disabled={operation.pending || revalidating || verifying} onChange={event => { setConfirmed(event.target.checked); setConfirmationError(false) }} />我已核对资料，确认直接创建已通过档案</label>
    {confirmationError ? <p role="alert">请先确认管理建档操作。</p> : null}
  </section>
  const controls = options.data ? { value: identity, onChange: changeIdentity, context: options.data } : undefined
  return <main className="peoplePage assistedPeoplePage"><h1>管理建档</h1>
    {context.can_direct_create_pending && !roleDenied && !operation.denied ? <>
      <button type="button" className="secondaryButton" aria-expanded={quickImport} disabled={operation.pending} onClick={() => { setQuickImportLoaded(true); setQuickImport(value => !value) }}>{quickImport ? "收起一键导入" : "一键导入建档及入住"}</button>
      {quickImportLoaded ? <div hidden={!quickImport}><Suspense fallback={<p role="status">正在加载导入工具…</p>}><QuickImportPanel accountId={accountId} enabled={context.can_direct_create_pending} revalidating={revalidating || verifying} /></Suspense></div> : null}
    </> : null}
    <p>这是显式管理建档：成功后直接形成已通过档案，不是普通待审核申请。不得把本操作当作监护验证、同意或家庭查看授权。</p>
    {operation.message ? <p role="status">{operation.message}</p> : null}
    {completed ? <><p>管理建档成功。</p><p>建档不等于账号激活。</p><p>已获批准的角色在本人完成资料、邮箱验证及激活后生效；超出建档人授权范围的角色需超级管理员单独批准，不影响基础账号激活。</p>
      {createdPersonId ? <CreatedReadiness accountId={accountId} personId={createdPersonId} /> : null}
      <button onClick={() => { pendingAttempt.current = null; strictAttempt.current = null; strictIdentity.current = emptyIdentityDraft(); setIdentity(emptyIdentityDraft()); operation.clearMessage(); setCreatedPersonId(null); setCompleted(false); setConfirmed(false); setFormEpoch((value) => value + 1) }}>开始新的管理建档</button></>
      : roleDenied ? <><p role="alert">当前无权设置激活角色，建档草稿已清除；请重新核实管理权限。</p><button type="button" onClick={() => void client.invalidateQueries({ queryKey: assistedPeopleKeys.account(accountId) })}>重新核实管理权限</button></> : !operation.denied ? <>
        <div role="group" aria-label="建档模式">
          <button type="button" className="secondaryButton" disabled={operation.pending || revalidating || verifying} aria-pressed={mode === "strict"} onClick={() => { if (mode !== "strict") { setIdentity(strictIdentity.current); setMode("strict"); setConfirmed(false) } }}>普通严格建档</button>
          {context.can_direct_create_pending ? <button type="button" className="secondaryButton" disabled={operation.pending || revalidating || verifying} aria-pressed={mode === "pending"} onClick={() => { if (mode !== "pending") { const pendingIdentity = restorePendingIdentity(client.getQueryData<SavedDraft>(draftKey)); setIdentity(pendingIdentity.value); setMigrationError(pendingIdentity.error); setMode("pending"); setConfirmed(false) } }}>资料待补建档</button> : null}
        </div>
        {options.isError ? <><p role="alert">暂时无法核实人员身份目录，不能提交；请恢复连接并重新核实。</p><button type="button" onClick={() => void options.refetch()}>重新核实人员身份</button></> : !options.data ? <p role="status">正在核实人员身份目录…</p> : null}
        {migrationError ? <p role="alert">{migrationError}</p> : null}
        {mode === "pending" && context.can_direct_create_pending ? <><button type="button" className="secondaryButton" disabled={operation.pending || revalidating} onClick={() => { client.removeQueries({ queryKey: draftKey }); pendingAttempt.current = null; setIdentity(emptyIdentityDraft()); setMigrationError(""); setConfirmed(false); setConfirmationError(false); operation.clearMessage(); setFormEpoch(value => value + 1) }}>清除待补草稿</button>{controls ? <PendingPersonForm key={`pending:${formEpoch}`} initialValue={client.getQueryData<SavedDraft>(draftKey)?.draft} submitting={operation.pending} readOnly={revalidating || verifying || Boolean(migrationError)} managementIdentity={controls} submissionConfirmation={confirmation} submitBlocked={blocked} coachOptions={context.eligible_coaches} teamOptions={context.eligible_teams}
          onDraftChange={(draft) => client.setQueryData<SavedDraft>(draftKey, (current) => ({ schemaVersion: 2, identity: current?.identity ?? identity, draft, attempt: current?.attempt }))}
          onSubmit={async (input) => {
            if (revalidating || roleBlocked) throw new Error("permissions_revalidating")
            if (!context.can_direct_create_pending) throw new Error("pending_creation_not_allowed")
            if (!confirmed) { setConfirmationError(true); throw new Error("confirmation_required") }
            const { kind: _kind, ...facts } = input
            void _kind
            const payload = { ...facts, ...normalizeIdentityInput(identity) }
            const fingerprint = intakePayloadFingerprint(payload)
            if (pendingAttempt.current?.fingerprint !== fingerprint) pendingAttempt.current = { fingerprint, key: newEditorOperationKey() }
            client.setQueryData<SavedDraft>(draftKey, (current) => ({ ...current, attempt: pendingAttempt.current! }))
            let personId: string | null = null
            const key = pendingAttempt.current.key
            if (await operation.run(async (signal) => { const result = await assistedPeopleApi.directCreatePending(payload, key, signal); personId = result.person_id })) {
              pendingAttempt.current = null; client.removeQueries({ queryKey: draftKey }); setCreatedPersonId(personId); setCompleted(true)
            } else throw new Error("request_failed")
          }} /> : null}</> : controls ? <AssistedPersonForm allowStaff key={`strict:${formEpoch}`} submitting={operation.pending} readOnly={revalidating || verifying || Boolean(migrationError)} managementIdentity={controls} submissionConfirmation={confirmation} submitBlocked={blocked} coachOptions={context.eligible_coaches} teamOptions={context.eligible_teams}
          onSubmit={async (input) => {
            if (revalidating || roleBlocked) throw new Error("permissions_revalidating")
            if (!confirmed) { setConfirmationError(true); throw new Error("confirmation_required") }
            const { kind: _kind, activation_role_keys: _roles, identity_selection: _selection, staff_teams: _teams, ...facts } = input
            void _kind; void _roles; void _selection; void _teams
            const payload = { ...facts, ...normalizeIdentityInput(identity) }
            const fingerprint = intakePayloadFingerprint(payload)
            if (strictAttempt.current?.fingerprint !== fingerprint) strictAttempt.current = { fingerprint, key: crypto.randomUUID() }
            const key = strictAttempt.current.key
            if (await operation.run((signal) => assistedPeopleApi.directCreate(payload, key, signal))) {
              strictAttempt.current = null; setCompleted(true)
            } else throw new Error("request_failed")
          }} /> : null}
      </> : null}
  </main>
}

function CreatedReadiness({ accountId, personId }: { accountId: string; personId: string }) {
  const query = useQuery({ queryKey: ["assisted-editor-readiness", accountId, personId], queryFn: ({ signal }) => assistedEditorApi.readiness(personId, signal), staleTime: 0, gcTime: 0, retry: false })
  if (query.isPending) return <p role="status">正在读取服务端待补资料…</p>
  if (query.isError || !query.data) return <p>未能读取服务端资料状态，请在有权限的人员详情中重新核实；不会推测资料已完整。</p>
  return <PendingReadinessSummary value={query.data} />
}
