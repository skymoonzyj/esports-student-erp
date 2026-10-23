import { useEffect, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useOptionalAuth } from "../auth/AuthProvider"
import { AssistedPersonForm } from "./AssistedPersonForm"
import { assistedEditorApi, newEditorOperationKey, useAssistedEditor, useEditorOperation } from "./editorApi"
import type { AssistedEditorContext, ProfileCorrectionInput } from "./editorApi"
import type { IntakeInput, PendingIntakeInput, PersonKind } from "./types"
import { PendingPersonForm, PendingReadinessSummary } from "./PendingPersonForm"
import type { PendingPersonDraft } from "./PendingPersonForm"
import { isPrivateDenial } from "./api"

export function AssistedProfileCorrectionPanel({ personId }: { personId: string }) {
  const auth = useOptionalAuth()
  if (!auth?.user || auth.status !== "authenticated") return null
  return <AccountCorrection key={`${auth.user.account_id}:${personId}`} accountId={auth.user.account_id} personId={personId} />
}

function AccountCorrection({ accountId, personId }: { accountId: string; personId: string }) {
  const client = useQueryClient()
  const editor = useAssistedEditor(accountId, personId)
  const [open, setOpen] = useState(() => Boolean(client.getQueryData(["assisted-editor-draft", accountId, personId])))
  const pendingAllowed = editor.context?.intake_mode === "pending" && editor.context.allowed_actions.includes("correct_pending_profile")
  const strictAllowed = editor.context?.intake_mode !== "pending" && editor.context?.kind !== "staff" && editor.context?.allowed_actions.includes("correct_profile")
  const privateDenied = editor.isError && isPrivateDenial(editor.error)
  useEffect(() => {
    if (editor.context && !pendingAllowed || privateDenied) client.removeQueries({ queryKey: ["assisted-editor-draft", accountId, personId] })
  }, [accountId, client, editor.context, pendingAllowed, personId, privateDenied])
  if (!editor.context || !pendingAllowed && !strictAllowed) {
    if (editor.isError && !privateDenied && client.getQueryData(["assisted-editor-draft", accountId, personId])) return <p role="alert">管理权限暂未核实，当前草稿已保留并隐藏。<button onClick={() => void editor.reload()}>重新核实管理权限</button></p>
    return null
  }
  return <section className="detailCard" aria-label="协助档案资料更正">
    <h2>本人及监护资料更正</h2>
    {editor.blocked ? <p role="alert">管理权限暂未核实，当前草稿已保留。<button onClick={() => void editor.reload()}>重新核实管理权限</button></p> : null}
    {!open ? <button className="secondaryButton" disabled={editor.blocked} onClick={() => setOpen(true)}>更正本人及监护资料</button> :
      pendingAllowed ? <PendingCorrectionDraft accountId={accountId} personId={personId} context={editor.context} blocked={editor.blocked} reload={editor.reload} onClose={() => { client.removeQueries({ queryKey: ["assisted-editor-draft", accountId, personId] }); setOpen(false) }} /> :
      editor.context.kind !== "staff" ? <CorrectionDraft accountId={accountId} personId={personId} context={{ ...editor.context, kind: editor.context.kind }} blocked={editor.blocked} reload={editor.reload} onClose={() => setOpen(false)} /> : null}
  </section>
}

function CorrectionDraft({ accountId, personId, context, blocked, reload, onClose }: {
  accountId: string; personId: string; context: AssistedEditorContext & { kind: PersonKind }; blocked: boolean;
  reload: () => Promise<AssistedEditorContext | null>; onClose: () => void
}) {
  const operation = useEditorOperation(accountId, personId, context.profile_version)
  const [kind] = useState(context.kind)
  const [reason, setReason] = useState("")
  const [reasonError, setReasonError] = useState(false)
  const [reviewedVersion, setReviewedVersion] = useState<number | null>(null)
  const [reviewing, setReviewing] = useState(false)
  const submitted = useRef<{ fingerprint: string; key: string } | null>(null)
  const locked = blocked || operation.pending || operation.conflict || operation.succeeded
  async function submit(input: IntakeInput) {
    if (locked) return
    if (!reason.trim() || reason.trim().length > 500) { setReasonError(true); return }
    setReasonError(false)
    const fields = { full_name: input.full_name, identity_number: input.identity_number, phone: input.phone,
      ...(context.can_edit_activation_email ? { email: input.email } : {}), sex: input.sex, date_of_birth: input.date_of_birth, guardians: input.guardians,
      expected_version: operation.version, reason: reason.trim() }
    const fingerprint = JSON.stringify(fields)
    if (submitted.current?.fingerprint !== fingerprint) submitted.current = { fingerprint, key: newEditorOperationKey() }
    const body: ProfileCorrectionInput = { ...fields, idempotency_key: submitted.current.key }
    await operation.run((signal) => assistedEditorApi.correct(personId, body, signal), "资料更正成功。")
  }
  return <>
    <p>请重新完整填写本人及监护资料，不会将脱敏信息作为原始资料。身份、所属队伍及教练不能在此更改。</p>
    <p>资料审核不代表监护验证、家庭同意或查看授权；既有已验证关系须使用原有授权流程处理。</p>
    <p>编辑基于档案版本 {operation.version}；当前管理信息版本 {context.profile_version}。</p>
    <label>资料更正原因<textarea value={reason} maxLength={500} disabled={locked} onChange={(event) => setReason(event.target.value)} autoComplete="off" /></label>
    {reasonError ? <p role="alert">请填写 1 至 500 字的资料更正原因，不要包含身份证或联系方式。</p> : null}
    {operation.message ? <p role={operation.succeeded ? "status" : "alert"}>{operation.message}</p> : null}
    {operation.conflict ? <div>
      <button disabled={reviewing} onClick={async () => { setReviewing(true); const latest = await reload(); setReviewedVersion(latest?.profile_version ?? null); setReviewing(false) }}>重新读取最新管理信息</button>
      {reviewedVersion !== null ? <><p>已重新读取版本 {reviewedVersion}，请核对现有关系及全部草稿；不会自动提交。</p><button onClick={() => { operation.acceptVersion(reviewedVersion); setReviewedVersion(null) }}>已核对，使用最新版本</button></> : null}
    </div> : null}
    <AssistedPersonForm identityOnlyKind={kind} canEditActivationEmail={context.can_edit_activation_email} onSubmit={submit} submitting={operation.pending} readOnly={locked} />
    <button className="secondaryButton" disabled={operation.pending} onClick={onClose}>关闭更正并清除草稿</button>
  </>
}

type SavedPendingCorrection = {
  draft?: PendingPersonDraft; reason?: string; version: number;
  attempt?: { fingerprint: string; key: string }; conflict?: boolean; message?: string
}
function PendingCorrectionDraft({ accountId, personId, context, blocked, reload, onClose }: {
  accountId: string; personId: string; context: AssistedEditorContext; blocked: boolean;
  reload: () => Promise<AssistedEditorContext | null>; onClose: () => void
}) {
  const client = useQueryClient()
  const draftKey = ["assisted-editor-draft", accountId, personId] as const
  const [saved] = useState(() => client.getQueryData<SavedPendingCorrection>(draftKey))
  const operation = useEditorOperation(accountId, personId, saved?.version ?? context.profile_version,
    saved ? { conflict: saved.conflict ?? false, message: saved.message ?? "" } : undefined)
  const [reason, setReason] = useState(saved?.reason ?? "")
  const [reasonError, setReasonError] = useState(false)
  const [identityConfirmed, setIdentityConfirmed] = useState(false)
  const [identityError, setIdentityError] = useState(false)
  const [reviewedVersion, setReviewedVersion] = useState<number | null>(null)
  const [reviewing, setReviewing] = useState(false)
  const submitted = useRef(saved?.attempt)
  const readiness = useQuery({ queryKey: ["assisted-editor-readiness", accountId, personId], queryFn: ({ signal }) => assistedEditorApi.readiness(personId, signal), staleTime: 0, gcTime: 0, retry: false })
  const locked = blocked || operation.pending || operation.conflict || operation.succeeded
  useEffect(() => {
    if (operation.succeeded) client.removeQueries({ queryKey: ["assisted-editor-draft", accountId, personId] })
    else if (operation.conflict) client.setQueryData<SavedPendingCorrection>(["assisted-editor-draft", accountId, personId], (current) => ({ ...current, version: operation.version, conflict: true, message: operation.message }))
  }, [accountId, client, operation.conflict, operation.message, operation.succeeded, operation.version, personId])
  function save(patch: Partial<SavedPendingCorrection>) {
    client.setQueryData<SavedPendingCorrection>(draftKey, (current) => ({ version: operation.version, ...current, ...patch }))
  }
  async function submit(input: PendingIntakeInput) {
    if (locked) return
    if (!reason.trim() || reason.trim().length > 500) { setReasonError(true); return }
    if (context.can_edit_activation_email && !identityConfirmed) { setIdentityError(true); return }
    setReasonError(false)
    // Pick facts explicitly: corrections cannot mutate kind, staff category, roles,
    // nickname, notes, housing, coach/team relationships or allocation state.
    const fields = { full_name: input.full_name, identity_number: input.identity_number, document_type: input.document_type,
      document_issuing_country: input.document_issuing_country, phone: input.phone,
      ...(context.can_edit_activation_email ? { email: input.email, identity_confirmed: identityConfirmed } : {}), sex: input.sex, date_of_birth: input.date_of_birth,
      guardians: input.guardians, expected_version: operation.version, reason: reason.trim() }
    const fingerprint = JSON.stringify(fields)
    if (submitted.current?.fingerprint !== fingerprint) submitted.current = { fingerprint, key: newEditorOperationKey() }
    save({ attempt: submitted.current })
    await operation.run((signal) => assistedEditorApi.correctPending(personId, { ...fields, idempotency_key: submitted.current!.key }, signal), "待补资料更正成功；账号仍未激活。")
  }
  return <>
    <p>仅更正明确的未激活待补档案。请重新填写真实本人及监护资料，不使用脱敏值；昵称、备注、住宿、身份、角色及归属不在此修改。</p>
    <p>编辑基于档案版本 {operation.version}；当前管理信息版本 {context.profile_version}。</p>
    {readiness.isPending ? <p role="status">正在读取服务端资料状态…</p> : readiness.isError || !readiness.data ? <p>暂未读到服务端缺项，不能推测资料完整或可入住。</p> : <PendingReadinessSummary value={readiness.data} />}
    <label>资料更正原因<textarea value={reason} maxLength={500} disabled={locked} autoComplete="off" onChange={(event) => { setReason(event.target.value); save({ reason: event.target.value }) }} /></label>
    {context.can_edit_activation_email ? <><p>邮箱更正将使原认领绑定与激活证明失效，须重新验证新邮箱并经管理员审核；不会创建账号。留空会明确清除邮箱，请核实真实事实。</p><label><input type="checkbox" checked={identityConfirmed} disabled={locked} onChange={(event) => { setIdentityConfirmed(event.target.checked); setIdentityError(false) }} />已通过可信渠道核实本人及邮箱更正</label></> : null}
    {identityError ? <p role="alert">请先通过可信渠道核实本人及邮箱更正。</p> : null}
    {reasonError ? <p role="alert">请填写 1 至 500 字的资料更正原因，不要包含证件或联系方式。</p> : null}
    {operation.message ? <p role={operation.succeeded ? "status" : "alert"}>{operation.message}</p> : null}
    {operation.conflict ? <div><button disabled={reviewing || blocked} onClick={async () => { setReviewing(true); const latest = await reload(); setReviewedVersion(latest?.profile_version ?? null); setReviewing(false) }}>重新读取最新管理信息</button>
      {reviewedVersion !== null ? <><p>已读取版本 {reviewedVersion}。请核对全部草稿及住宿/联系方式限制；不会自动提交。</p><button onClick={() => { operation.acceptVersion(reviewedVersion); save({ version: reviewedVersion, conflict: false, message: "" }); setReviewedVersion(null) }}>已核对，使用最新版本</button></> : null}
    </div> : null}
    <PendingPersonForm initialValue={saved?.draft} identityOnlyKind={context.kind} canEditActivationEmail={context.can_edit_activation_email} onDraftChange={(draft) => save({ draft })} submitting={operation.pending} readOnly={locked} onSubmit={submit} />
    <button className="secondaryButton" disabled={operation.pending} onClick={onClose}>关闭更正并清除草稿</button>
  </>
}
