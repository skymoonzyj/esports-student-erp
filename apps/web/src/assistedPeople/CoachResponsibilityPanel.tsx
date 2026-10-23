import { useState } from "react"
import { useOptionalAuth } from "../auth/AuthProvider"
import { assistedEditorApi, useAssistedEditor, useEditorOperation } from "./editorApi"
import type { AssistedEditorContext } from "./editorApi"

export function CoachResponsibilityPanel({ personId }: { personId: string }) {
  const auth = useOptionalAuth()
  if (!auth?.user || auth.status !== "authenticated") return null
  return <AccountCoaches key={`${auth.user.account_id}:${personId}`} accountId={auth.user.account_id} personId={personId} />
}

function AccountCoaches({ accountId, personId }: { accountId: string; personId: string }) {
  const editor = useAssistedEditor(accountId, personId)
  const [open, setOpen] = useState(false)
  if (!editor.context?.allowed_actions.includes("replace_coaches")) return null
  return <section className="detailCard" aria-label="负责教练维护"><h2>负责教练</h2>
    <p>当前负责：{editor.context.current_coach_person_ids.map((id, index) => editor.context?.eligible_coaches.find((coach) => coach.id === id)?.label ?? `不可用教练 ${index + 1}`).join("、") || "无"}</p>
    {editor.blocked ? <p role="alert">管理权限暂未核实，当前草稿已保留。<button onClick={() => void editor.reload()}>重新核实管理权限</button></p> : null}
    {!open ? <button className="secondaryButton" disabled={editor.blocked} onClick={() => setOpen(true)}>变更负责教练</button> :
      <CoachDraft accountId={accountId} personId={personId} context={editor.context} blocked={editor.blocked} reload={editor.reload} onClose={() => setOpen(false)} />}
  </section>
}

function CoachDraft({ accountId, personId, context, blocked, reload, onClose }: {
  accountId: string; personId: string; context: AssistedEditorContext; blocked: boolean;
  reload: () => Promise<AssistedEditorContext | null>; onClose: () => void
}) {
  const operation = useEditorOperation(accountId, personId, context.profile_version)
  const [selected, setSelected] = useState(() => [...context.current_coach_person_ids])
  const [reason, setReason] = useState("")
  const [confirmed, setConfirmed] = useState(false)
  const [reviewedVersion, setReviewedVersion] = useState<number | null>(null)
  const [reviewing, setReviewing] = useState(false)
  const unavailable = selected.filter((id) => !context.eligible_coaches.some((coach) => coach.id === id))
  const locked = blocked || operation.pending || operation.conflict || operation.succeeded
  const valid = confirmed && reason.trim().length > 0 && reason.trim().length <= 200 && !unavailable.length
  function select(ids: string[]) { setSelected(ids); setConfirmed(false) }
  return <>
    <p>编辑基于档案版本 {operation.version}；变更只维护负责关系，不授予账号角色或队伍权限。</p>
    <fieldset disabled={locked}><legend>当前可选教练</legend>
      {context.eligible_coaches.map((coach) => <label key={coach.id}><input type="checkbox" checked={selected.includes(coach.id)} onChange={(event) => select(event.target.checked ? [...selected, coach.id] : selected.filter((id) => id !== coach.id))} />{coach.label}</label>)}
      {unavailable.map((id, index) => <button type="button" key={id} onClick={() => select(selected.filter((item) => item !== id))}>移除不可用教练 {index + 1}</button>)}
      <label>教练变更原因<textarea maxLength={200} value={reason} onChange={(event) => { setReason(event.target.value); setConfirmed(false) }} autoComplete="off" /></label>
      <label><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />确认按当前选择变更负责教练</label>
    </fieldset>
    <p>原因须为 1 至 200 字，不要包含身份证、联系方式或秘密。</p>
    {operation.message ? <p role={operation.succeeded ? "status" : "alert"}>{operation.message}</p> : null}
    {operation.conflict ? <div><button disabled={reviewing} onClick={async () => { setReviewing(true); const latest = await reload(); setReviewedVersion(latest?.profile_version ?? null); setReviewing(false) }}>重新读取最新管理信息</button>
      {reviewedVersion !== null ? <><p>已重新读取版本 {reviewedVersion}，请核对当前负责关系和草稿后确认。</p><button onClick={() => { operation.acceptVersion(reviewedVersion); setReviewedVersion(null); setConfirmed(false) }}>已核对，使用最新版本</button></> : null}
    </div> : null}
    <button className="primaryButton" disabled={locked || !valid} onClick={() => {
      if (!valid || locked) return
      void operation.run((signal) => assistedEditorApi.coaches(personId, { coach_person_ids: [...new Set(selected)], expected_version: operation.version, reason: reason.trim() }, signal), "负责教练变更成功。")
    }}>确认变更教练</button>
    <button className="secondaryButton" disabled={operation.pending} onClick={onClose}>关闭教练变更</button>
  </>
}
