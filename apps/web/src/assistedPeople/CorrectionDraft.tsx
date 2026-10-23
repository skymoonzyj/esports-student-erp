import { useRef, useState } from "react"
import { AssistedPersonForm } from "./AssistedPersonForm"
import { assistedEditorApi, newEditorOperationKey, useEditorOperation } from "./editorApi"
import type { AssistedEditorContext, ProfileCorrectionInput } from "./editorApi"
import type { IntakeInput } from "./types"

export function CorrectionDraft({ accountId, personId, context, blocked, reload, onClose }: {
  accountId: string; personId: string; context: AssistedEditorContext; blocked: boolean;
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
      email: input.email, sex: input.sex, date_of_birth: input.date_of_birth, guardians: input.guardians,
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
    <AssistedPersonForm identityOnlyKind={kind} onSubmit={submit} submitting={operation.pending} readOnly={locked} />
    <button className="secondaryButton" disabled={operation.pending} onClick={onClose}>关闭更正并清除草稿</button>
  </>
}
