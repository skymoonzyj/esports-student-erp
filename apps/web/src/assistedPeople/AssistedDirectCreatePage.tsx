import { useEffect, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useAuth } from "../auth/AuthProvider"
import { AssistedPersonForm } from "./AssistedPersonForm"
import { assistedPeopleApi } from "./api"
import type { AssistedContext } from "./api"
import { useAssistedOperation, useAssistedPeopleContext } from "./useAssistedPeopleContext"
import { PendingPersonForm, PendingReadinessSummary } from "./PendingPersonForm"
import type { PendingPersonDraft } from "./PendingPersonForm"
import { assistedEditorApi, newEditorOperationKey } from "./editorApi"

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
  const client = useQueryClient()
  const draftKey = ["assisted-people", accountId, "pending-create-draft"] as const
  type SavedDraft = { draft?: PendingPersonDraft; attempt?: { fingerprint: string; key: string } }
  const [mode, setMode] = useState<"strict" | "pending">(() => context.can_direct_create_pending && client.getQueryData<SavedDraft>(draftKey) ? "pending" : "strict")
  const [confirmed, setConfirmed] = useState(false)
  const [completed, setCompleted] = useState(false)
  const [formEpoch, setFormEpoch] = useState(0)
  const [confirmationError, setConfirmationError] = useState(false)
  const [createdPersonId, setCreatedPersonId] = useState<string | null>(null)
  const pendingAttempt = useRef<{ fingerprint: string; key: string } | null>(context.can_direct_create_pending ? client.getQueryData<SavedDraft>(draftKey)?.attempt ?? null : null)
  const strictAttempt = useRef<{ fingerprint: string; key: string } | null>(null)
  const operation = useAssistedOperation(accountId)
  const blocked = operation.pending || revalidating
  useEffect(() => { if (operation.denied) { pendingAttempt.current = null; strictAttempt.current = null; client.removeQueries({ queryKey: ["assisted-people", accountId, "pending-create-draft"] }) } }, [accountId, client, operation.denied])
  useEffect(() => {
    if (!context.can_direct_create_pending) {
      pendingAttempt.current = null
      if (mode === "pending") { setMode("strict"); setConfirmed(false); setConfirmationError(false) }
    }
  }, [context.can_direct_create_pending, mode])
  return <main className="peoplePage"><h1>管理建档</h1>
    <p>这是显式管理建档：成功后直接形成已通过档案，不是普通待审核申请。不得把本操作当作监护验证、同意或家庭查看授权。</p>
    {operation.message ? <p role="status">{operation.message}</p> : null}
    {completed ? <><p>管理建档成功。</p><p>建档不等于账号激活。</p>
      {createdPersonId ? <CreatedReadiness accountId={accountId} personId={createdPersonId} /> : null}
      <button onClick={() => { pendingAttempt.current = null; strictAttempt.current = null; operation.clearMessage(); setCreatedPersonId(null); setCompleted(false); setConfirmed(false); setFormEpoch((value) => value + 1) }}>开始新的管理建档</button></>
      : !operation.denied ? <>
        <div role="group" aria-label="建档模式">
          <button type="button" className="secondaryButton" disabled={blocked} aria-pressed={mode === "strict"} onClick={() => { setMode("strict"); setConfirmed(false) }}>普通严格建档</button>
          {context.can_direct_create_pending ? <button type="button" className="secondaryButton" disabled={blocked} aria-pressed={mode === "pending"} onClick={() => { setMode("pending"); setConfirmed(false) }}>资料待补建档</button> : null}
        </div>
        <label><input type="checkbox" checked={confirmed} disabled={blocked} onChange={(event) => { setConfirmed(event.target.checked); setConfirmationError(false) }} />我已核对资料，确认直接创建已通过档案</label>
        {confirmationError ? <p role="alert">请先确认管理建档操作。</p> : null}
        {mode === "pending" && context.can_direct_create_pending ? <><button type="button" className="secondaryButton" disabled={blocked} onClick={() => { client.removeQueries({ queryKey: draftKey }); pendingAttempt.current = null; setConfirmed(false); setConfirmationError(false); operation.clearMessage(); setFormEpoch(value => value + 1) }}>清除待补草稿</button><PendingPersonForm key={`pending:${formEpoch}`} initialValue={client.getQueryData<SavedDraft>(draftKey)?.draft} submitting={blocked} coachOptions={context.eligible_coaches} teamOptions={context.eligible_teams}
          onDraftChange={(draft) => client.setQueryData<SavedDraft>(draftKey, (current) => ({ ...current, draft }))}
          onSubmit={async (input) => {
            if (revalidating) throw new Error("permissions_revalidating")
            if (!context.can_direct_create_pending) throw new Error("pending_creation_not_allowed")
            if (!confirmed) { setConfirmationError(true); throw new Error("confirmation_required") }
            const fingerprint = JSON.stringify(input)
            if (pendingAttempt.current?.fingerprint !== fingerprint) pendingAttempt.current = { fingerprint, key: newEditorOperationKey() }
            client.setQueryData<SavedDraft>(draftKey, (current) => ({ ...current, attempt: pendingAttempt.current! }))
            let personId: string | null = null
            const key = pendingAttempt.current.key
            if (await operation.run(async (signal) => { const result = await assistedPeopleApi.directCreatePending(input, key, signal); personId = result.person_id })) {
              pendingAttempt.current = null; client.removeQueries({ queryKey: draftKey }); setCreatedPersonId(personId); setCompleted(true)
            } else throw new Error("request_failed")
          }} /></> : <AssistedPersonForm key={`strict:${formEpoch}`} submitting={blocked} coachOptions={context.eligible_coaches} teamOptions={context.eligible_teams}
          onSubmit={async (input) => {
            if (revalidating) throw new Error("permissions_revalidating")
            if (!confirmed) { setConfirmationError(true); throw new Error("confirmation_required") }
            const fingerprint = JSON.stringify(input)
            if (strictAttempt.current?.fingerprint !== fingerprint) strictAttempt.current = { fingerprint, key: crypto.randomUUID() }
            const key = strictAttempt.current.key
            if (await operation.run((signal) => assistedPeopleApi.directCreate(input, key, signal))) {
              strictAttempt.current = null; setCompleted(true)
            } else throw new Error("request_failed")
          }} />}
      </> : null}
  </main>
}

function CreatedReadiness({ accountId, personId }: { accountId: string; personId: string }) {
  const query = useQuery({ queryKey: ["assisted-editor-readiness", accountId, personId], queryFn: ({ signal }) => assistedEditorApi.readiness(personId, signal), staleTime: 0, gcTime: 0, retry: false })
  if (query.isPending) return <p role="status">正在读取服务端待补资料…</p>
  if (query.isError || !query.data) return <p>未能读取服务端资料状态，请在有权限的人员详情中重新核实；不会推测资料已完整。</p>
  return <PendingReadinessSummary value={query.data} />
}
