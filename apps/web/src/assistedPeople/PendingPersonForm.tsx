import { useId, useRef, useState } from "react"
import type { FormEvent, ReactNode } from "react"
import { z } from "zod"
import { isDateInput, todayDateInput } from "../shared/date"
import { isAssistedAdult } from "./calendarAge"
import type { AssistedAssignmentOption } from "./AssistedPersonForm"
import type { AssistedReadiness } from "./editorApi"
import type { AssistedGuardianInput, PendingIntakeInput, PendingPersonKind } from "./types"
import "../people/people.css"
import "./assisted-people.css"

type GuardianDraft = Omit<AssistedGuardianInput, "sex"> & { sex: "male" | "female" | "" }
export type PendingPersonDraft = Omit<PendingIntakeInput, "guardians"> & { guardians: GuardianDraft[] }
interface Props {
  initialValue?: PendingPersonDraft
  onDraftChange?: (value: PendingPersonDraft) => void
  onSubmit: (value: PendingIntakeInput) => Promise<void>
  submitting: boolean
  readOnly?: boolean
  identityOnlyKind?: PendingPersonKind
  canEditActivationEmail?: boolean
  coachOptions?: readonly AssistedAssignmentOption[]
  teamOptions?: readonly AssistedAssignmentOption[]
}
const weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
function cnFacts(value: string) {
  const number = value.trim().toUpperCase()
  if (!/^\d{17}[\dX]$/.test(number)) return null
  const birth = `${number.slice(6, 10)}-${number.slice(10, 12)}-${number.slice(12, 14)}`
  const sum = [...number.slice(0, 17)].reduce((total, digit, index) => total + Number(digit) * weights[index], 0)
  if (!isDateInput(birth) || birth > todayDateInput() || number[17] !== "10X98765432"[sum % 11]) return null
  return { date_of_birth: birth, sex: Number(number[16]) % 2 ? "male" as const : "female" as const }
}
function blankDraft(kind: PendingPersonKind = "staff"): PendingPersonDraft {
  return { kind, full_name: "", identity_number: "", document_type: "cn_id", document_issuing_country: null,
    sex: null, date_of_birth: null, phone: null, email: null, guardians: [], coach_person_ids: [], team_id: null, team_allocation_status: "pending" }
}
function Field({ id, label, error, children }: { id: string; label: string; error?: string; children: ReactNode }) {
  return <div className="formField"><label htmlFor={id}>{label}</label>{children}
    {error ? <small className="fieldError" id={`${id}-error`}>{error}</small> : null}</div>
}

export function PendingPersonForm({ initialValue, onDraftChange, onSubmit, submitting, readOnly = false, identityOnlyKind,
  canEditActivationEmail = true, coachOptions = [], teamOptions = [] }: Props) {
  const [draft, setDraft] = useState<PendingPersonDraft>(() => initialValue ?? blankDraft(identityOnlyKind))
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [pending, setPending] = useState(false)
  const [submitError, setSubmitError] = useState(false)
  const flight = useRef(false)
  const id = useId()
  const busy = submitting || pending
  function change(patch: Partial<PendingPersonDraft>) {
    const next = { ...draft, ...patch }
    setDraft(next); onDraftChange?.(next)
  }
  function guardianChange(index: number, patch: Partial<GuardianDraft>) {
    change({ guardians: draft.guardians.map((item, position) => position === index ? { ...item, ...patch } : item) })
  }
  function input(key: "full_name" | "identity_number" | "phone" | "email" | "date_of_birth" | "document_issuing_country", label: string) {
    const fieldId = `${id}-${key}`
    return <Field id={fieldId} label={label} error={errors[key]}><input id={fieldId} autoComplete="off"
      type={key === "date_of_birth" ? "date" : key === "email" ? "email" : key === "phone" ? "tel" : "text"}
      maxLength={key === "full_name" || key === "identity_number" ? 80 : key === "document_issuing_country" ? 2 : undefined}
      value={key === "email" && !canEditActivationEmail ? "" : draft[key] ?? ""} disabled={key === "email" && !canEditActivationEmail}
      aria-invalid={Boolean(errors[key])} aria-describedby={errors[key] ? `${fieldId}-error` : undefined}
      onChange={(event) => {
        if (key === "email" && !canEditActivationEmail) return
        const value = event.target.value
        const facts = key === "identity_number" && draft.document_type === "cn_id" ? cnFacts(value) : null
        change({ [key]: value, ...(facts ?? {}) })
      }} /></Field>
  }
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy || readOnly || flight.current) return
    const next: Record<string, string> = {}
    const today = todayDateInput()
    if (!draft.full_name.trim() || draft.full_name.trim().length > 80) next.full_name = "请填写 1 至 80 字的真实姓名"
    const identity = draft.document_type === "cn_id" ? draft.identity_number.trim().toUpperCase() : draft.identity_number.trim()
    if (draft.document_type === "cn_id") {
      const facts = cnFacts(identity)
      if (!facts) next.identity_number = "请填写有效的 18 位身份证号码（含校验码）"
      if (!draft.sex || facts && facts.sex !== draft.sex) next.sex = "性别须与有效身份证一致"
      if (!draft.date_of_birth || facts && facts.date_of_birth !== draft.date_of_birth) next.date_of_birth = "出生日期须与有效身份证一致"
    } else {
      if (!identity || identity.length > 80 || [...identity].some((char) => char.charCodeAt(0) < 32)) next.identity_number = "请填写真实有效的护照号码"
      if (draft.document_issuing_country?.trim() && !/^[A-Z]{2}$/.test(draft.document_issuing_country.trim().toUpperCase())) next.document_issuing_country = "请填写两位国家代码；未知请留空"
    }
    if (draft.date_of_birth && (!isDateInput(draft.date_of_birth) || draft.date_of_birth > today)) next.date_of_birth = "请填写有效且不晚于今天的出生日期"
    const phone = draft.phone?.trim()
    if (phone && (phone.length > 80 || [...phone].some((char) => char.charCodeAt(0) < 32) || draft.document_type === "cn_id" && !/^1[3-9]\d{9}$/.test(phone))) next.phone = "请填写有效的联系电话；暂缺请留空"
    if (canEditActivationEmail && draft.email?.trim() && !z.email().safeParse(draft.email.trim()).success) next.email = "邮箱格式不正确"
    if (!identityOnlyKind) {
      if (draft.coach_person_ids.some((coachId) => !coachOptions.some((option) => option.id === coachId && !option.disabled))) next.coach_person_ids = "已选教练不可用，请移除后重新选择"
      if (draft.team_id && !teamOptions.some((option) => option.id === draft.team_id && !option.disabled)) next.team_id = "已选队伍不可用，请重新选择或留空"
      if (draft.team_id && draft.team_allocation_status === "not_required") next.team_id = "无需分队时不能同时选择所属队伍；请核实实际归属"
    }
    draft.guardians.forEach((guardian, index) => {
      const prefix = `guardians.${index}.`
      const facts = cnFacts(guardian.identity_number)
      if (!guardian.full_name.trim() || guardian.full_name.trim().length > 80) next[`${prefix}full_name`] = "请填写监护人真实姓名"
      if (!facts) next[`${prefix}identity_number`] = "请填写有效的成年监护人身份证"
      if (!guardian.sex || facts && facts.sex !== guardian.sex) next[`${prefix}sex`] = "性别须与监护人身份证一致"
      if (!isDateInput(guardian.date_of_birth) || facts && facts.date_of_birth !== guardian.date_of_birth || !isAssistedAdult(guardian.date_of_birth, today)) next[`${prefix}date_of_birth`] = "监护人须年满 18 周岁且出生日期与身份证一致"
      if (!/^1[3-9]\d{9}$/.test(guardian.phone.trim())) next[`${prefix}phone`] = "请填写监护人有效手机号"
      if (guardian.relationship.trim().length < 2 || guardian.relationship.trim().length > 40) next[`${prefix}relationship`] = "与本人关系须为 2 至 40 字"
      if (guardian.email?.trim() && (!z.email().safeParse(guardian.email.trim()).success || guardian.email.trim().toLowerCase() === draft.email?.trim().toLowerCase())) next[`${prefix}email`] = "监护人邮箱须有效且与本人不同"
    })
    setErrors(next); setSubmitError(false)
    if (Object.keys(next).length) return
    const value: PendingIntakeInput = {
      kind: identityOnlyKind ?? draft.kind, full_name: draft.full_name.trim(), identity_number: draft.document_type === "passport" ? draft.identity_number : identity,
      document_type: draft.document_type, document_issuing_country: draft.document_type === "passport" ? draft.document_issuing_country?.trim().toUpperCase() || null : null,
      sex: draft.sex, date_of_birth: draft.date_of_birth || null, phone: draft.phone?.trim() || null,
      email: canEditActivationEmail ? draft.email?.trim().toLowerCase() || null : null,
      guardians: draft.guardians.map((guardian) => ({ full_name: guardian.full_name.trim(), identity_number: guardian.identity_number.trim().toUpperCase(),
        phone: guardian.phone.trim(), email: guardian.email?.trim().toLowerCase() || null, sex: guardian.sex as AssistedGuardianInput["sex"],
        date_of_birth: guardian.date_of_birth, relationship: guardian.relationship.trim(), is_primary: guardian.is_primary ?? false })),
      coach_person_ids: identityOnlyKind ? [] : [...new Set(draft.coach_person_ids)], team_id: identityOnlyKind ? null : draft.team_id,
      team_allocation_status: draft.team_allocation_status,
    }
    flight.current = true; setPending(true)
    try { await onSubmit(value) } catch { setSubmitError(true) }
    finally { flight.current = false; setPending(false) }
  }
  return <form className="personFormCard assistedPersonForm" onSubmit={submit} noValidate autoComplete="off" aria-busy={busy}>
    <p className="assistedFormNotice">资料待补建档不创建账号或授予角色。缺项留空，不得用虚假联系方式、监护人或归属代填。草稿仅保存在当前会话内存。</p>
    <p className="assistedFormNotice">预填邮箱也须本人验证并经管理员核对认领；资料完整、账号激活与队伍归属是独立状态。</p>
    {!draft.date_of_birth ? <p>年龄待补</p> : null}
    {!draft.sex ? <p>性别待补，暂不能办理实际入住；目标房间不等于已入住。</p> : null}
    {!canEditActivationEmail ? <p>激活邮箱受保护，此处不会更改或清空。请使用认领审核或原有验证变更流程。</p> : null}
    <fieldset className="assistedFormBody" disabled={busy || readOnly}><legend>{identityOnlyKind ? "待补本人及监护资料更正" : "资料待补建档"}</legend>
      {!identityOnlyKind ? <Field id={`${id}-kind`} label="人员身份"><select id={`${id}-kind`} value={draft.kind} onChange={(event) => {
        const kind = event.target.value as PendingPersonKind
        change({ kind, coach_person_ids: kind === "student" || kind === "both" ? draft.coach_person_ids : [], team_id: kind === "player" || kind === "both" ? draft.team_id : null })
      }}>
        <option value="staff">其他工作人员</option><option value="student">学员</option><option value="player">队员</option><option value="both">学员兼队员</option>
      </select></Field> : null}
      <div className="formGrid">{input("full_name", "姓名")}
        <Field id={`${id}-document`} label="证件类型"><select id={`${id}-document`} value={draft.document_type} onChange={(event) => change({ document_type: event.target.value as PendingIntakeInput["document_type"], identity_number: "", sex: null, date_of_birth: null, document_issuing_country: null })}>
          <option value="cn_id">中国居民身份证</option><option value="passport">护照</option></select></Field>
        {input("identity_number", draft.document_type === "cn_id" ? "身份证" : "护照号码")}
        {draft.document_type === "passport" ? input("document_issuing_country", "签发国家（两位代码，可暂缺）") : null}
        <Field id={`${id}-sex`} label="性别" error={errors.sex}><select id={`${id}-sex`} value={draft.sex ?? ""} aria-invalid={Boolean(errors.sex)} aria-describedby={errors.sex ? `${id}-sex-error` : undefined} onChange={(event) => change({ sex: event.target.value as PendingIntakeInput["sex"] || null })}>
          <option value="">待补</option><option value="male">男</option><option value="female">女</option></select></Field>
        {input("date_of_birth", "出生日期")}{input("phone", "联系电话（可暂缺）")}{input("email", "邮箱")}
      </div>
      {!identityOnlyKind ? <>
        {draft.kind === "student" || draft.kind === "both" ? <fieldset className="assistedAssignments"><legend>负责教练（可暂缺）</legend>
          {coachOptions.map((option) => <label key={option.id}><input type="checkbox" disabled={option.disabled} checked={draft.coach_person_ids.includes(option.id)} onChange={(event) => change({ coach_person_ids: event.target.checked ? [...draft.coach_person_ids, option.id] : draft.coach_person_ids.filter((item) => item !== option.id) })} />{option.label}</label>)}
          {draft.coach_person_ids.filter((selected) => !coachOptions.some((option) => option.id === selected && !option.disabled)).map((selected, index) => <button key={selected} type="button" onClick={() => change({ coach_person_ids: draft.coach_person_ids.filter((item) => item !== selected) })}>移除不可用教练 {index + 1}</button>)}
          {errors.coach_person_ids ? <p role="alert">{errors.coach_person_ids}</p> : null}
        </fieldset> : null}
        {draft.kind === "player" || draft.kind === "both" ? <Field id={`${id}-team`} label="所属队伍（可暂缺）" error={errors.team_id}><select id={`${id}-team`} value={draft.team_id ?? ""} aria-invalid={Boolean(errors.team_id)} onChange={(event) => change({ team_id: event.target.value || null })}>
          <option value="">暂不选择</option>{teamOptions.map((option) => <option key={option.id} value={option.id} disabled={option.disabled}>{option.label}</option>)}</select></Field> : null}
        <Field id={`${id}-allocation`} label="队伍归属状态"><select id={`${id}-allocation`} value={draft.team_allocation_status} onChange={(event) => change({ team_allocation_status: event.target.value as PendingIntakeInput["team_allocation_status"] })}>
          <option value="pending">待划分队伍</option><option value="not_required">无需分队</option></select></Field>
        <p className="assistedFormNotice">选择真实队伍后由服务端记录已划分。无需分队时不能同时选择队伍；不会建立虚拟队伍。</p>
      </> : null}
      <section className="formSection" aria-label="待补监护资料"><h2>监护人（可暂缺）</h2><p>未成年人暂缺监护资料可先建档；激活前仍须补齐有效成年监护资料。监护资料不等于监护授权。</p>
        {draft.guardians.map((guardian, index) => <fieldset key={index} className="assistedGuardian"><legend>监护人 {index + 1}</legend><div className="formGrid">
          {([ ["full_name", "姓名"], ["identity_number", "身份证"], ["phone", "手机号"], ["email", "邮箱"], ["date_of_birth", "出生日期"], ["relationship", "与本人关系"] ] as const).map(([key, label]) => {
            const fieldId = `${id}-guardian-${index}-${key}`, error = errors[`guardians.${index}.${key}`]
            return <Field key={key} id={fieldId} label={`监护人${label}`} error={error}><input id={fieldId} autoComplete="off" type={key === "date_of_birth" ? "date" : "text"} value={guardian[key] ?? ""} aria-invalid={Boolean(error)} aria-describedby={error ? `${fieldId}-error` : undefined} onChange={(event) => guardianChange(index, { [key]: event.target.value, ...(key === "identity_number" ? cnFacts(event.target.value) ?? {} : {}) })} /></Field>
          })}
          <Field id={`${id}-guardian-${index}-sex`} label="监护人性别" error={errors[`guardians.${index}.sex`]}><select id={`${id}-guardian-${index}-sex`} value={guardian.sex} aria-invalid={Boolean(errors[`guardians.${index}.sex`])} onChange={(event) => guardianChange(index, { sex: event.target.value as GuardianDraft["sex"] })}><option value="">请选择</option><option value="male">男</option><option value="female">女</option></select></Field>
        </div><label><input type="checkbox" checked={guardian.is_primary ?? false} onChange={(event) => guardianChange(index, { is_primary: event.target.checked })} />主要监护人</label>
          <button type="button" className="secondaryButton" onClick={() => change({ guardians: draft.guardians.filter((_, position) => position !== index) })}>移除监护人 {index + 1}</button></fieldset>)}
        <button type="button" className="secondaryButton" onClick={() => change({ guardians: [...draft.guardians, { full_name: "", identity_number: "", phone: "", email: null, sex: "", date_of_birth: "", relationship: "", is_primary: draft.guardians.length === 0 }] })}>添加监护人</button>
      </section>
    </fieldset>
    {submitError ? <p role="alert">提交未成功，当前草稿已保留；请检查服务器提示后重试。</p> : null}
    <div className="personFormActions"><button className="primaryButton" disabled={busy || readOnly} type="submit">{busy ? "正在提交…" : identityOnlyKind ? "提交待补更正" : "建立待补档案"}</button></div>
  </form>
}

const missingLabels: Record<string, string> = { phone: "联系电话", email: "独立邮箱", sex: "性别", date_of_birth: "出生日期", document_issuing_country: "签发国家", guardian: "有效监护资料", guardian_age: "监护年龄待核对", assisted_approval: "有效建档审批", assisted_provenance: "建档来源核对", assisted_profile: "档案事实核对" }
export function PendingReadinessSummary({ value }: { value: AssistedReadiness }) {
  const labels = (items: string[]) => items.map((item) => missingLabels[item] ?? "其他服务端待补项").join("、") || "无"
  return <section aria-label="服务端资料状态">
    <p>账号未激活：建档审批及资料补齐均不等于账号启用。预填邮箱也须本人验证并经管理员核对认领。</p>
    <p>资料待补：{labels(value.profile_missing)}</p><p>激活待补：{labels(value.activation_missing)}</p>
    <p>{value.housing_ready ? "可办理住宿，仍须通过房间、容量、性别及时间校验。" : `暂不能办理住宿：${labels(value.housing_missing)}`}</p>
  </section>
}
