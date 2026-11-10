import { useId, useRef, useState } from "react"
import type { FormEvent, ReactNode } from "react"
import { z } from "zod"

import { isDateInput, todayDateInput } from "../shared/date"
import type { AssistedGuardianInput, IntakeInput, PersonKind, PendingPersonKind, DirectCreateInput } from "./types"
import { IntakeIdentitySection, identityValidation } from "./IntakeIdentitySection"
import type { ManagementIdentityControls } from "./IntakeIdentitySection"
import { identityKind } from "./intakeIdentity"
import { isAssistedAdult } from "./calendarAge"
import { birthdayAgeText, cnDocumentFacts, documentDemographicNotice, documentNumberLabel, documentTypeLabel, identityDocumentTypes, normalizeIdentityDocument } from "./documentFacts"
import type { IdentityDocumentType } from "./documentFacts"
import "../people/people.css"
import "./assisted-people.css"

export interface AssistedAssignmentOption {
  id: string
  label: string
  disabled?: boolean
}

interface CommonProps {
  /** Applied only on mount. Mount with the account ID as key when accounts change. */
  initialValue?: IntakeInput
  submitting: boolean
  coachOptions?: readonly AssistedAssignmentOption[]
  teamOptions?: readonly AssistedAssignmentOption[]
  readOnly?: boolean
  canEditActivationEmail?: boolean
}
export type AssistedPersonFormProps = CommonProps & (
  /** Explicit managed correction mode also supports immutable staff identity. */
  ({ allowStaff: true; onSubmit: (input: DirectCreateInput) => Promise<void> } & (
    { identityOnlyKind?: undefined; managementIdentity?: ManagementIdentityControls; submissionConfirmation?: ReactNode; submitBlocked?: boolean } |
    { identityOnlyKind: PendingPersonKind; managementIdentity?: never; submissionConfirmation?: never; submitBlocked?: never }
  )) |
  { allowStaff?: false; identityOnlyKind?: PersonKind; onSubmit: (input: IntakeInput) => Promise<void> }
)

type PersonDraft = Omit<IntakeInput, "sex" | "guardians" | "kind"> & {
  kind: PendingPersonKind
  sex: IntakeInput["sex"] | ""
  guardians: GuardianDraft[]
}
type GuardianDraft = Omit<AssistedGuardianInput, "sex"> & { sex: AssistedGuardianInput["sex"] | "" }
type Errors = Record<string, string>

function initialDraft(value?: IntakeInput): PersonDraft {
  return value ? {
    kind: value.kind, full_name: value.full_name, identity_number: value.identity_number,
    document_type: value.document_type, document_issuing_country: value.document_issuing_country,
    phone: value.phone, email: value.email, sex: value.sex, date_of_birth: value.date_of_birth, team_id: value.team_id,
    coach_person_ids: [...value.coach_person_ids],
    guardians: value.guardians.map((guardian) => ({ ...guardian })),
  } : {
    kind: "student", full_name: "", identity_number: "", sex: "", date_of_birth: "",
    phone: "", email: "", guardians: [], coach_person_ids: [], team_id: null,
  }
}

function validatePerson(value: PersonDraft | GuardianDraft, prefix: string, today: string, errors: Errors) {
  const guardian = Boolean(prefix)
  const label = guardian ? "监护人" : ""
  if (!value.full_name.trim()) errors[`${prefix}full_name`] = `请填写${label}姓名`
  else if (value.full_name.trim().length > 80) errors[`${prefix}full_name`] = "姓名不能超过 80 个字"
  const identity = value.identity_number.trim().toUpperCase()
  const cn = (value.document_type ?? "cn_id") === "cn_id"
  const facts = cn ? cnDocumentFacts(identity, today) : null
  const validIdentity = Boolean(normalizeIdentityDocument(value.identity_number, value.document_type))
  if (!validIdentity) errors[`${prefix}identity_number`] = value.document_type === "hk_mo_permit" ? "请填写有效的回乡证号码（H/M 加 8 位数字，可附两位换证次数）" : value.document_type === "hk_id" ? "请填写有效的香港身份证号码（含校验字符）" : "请填写有效的 18 位身份证号码（含校验码）"
  if (!value.sex) errors[`${prefix}sex`] = `请选择${label}性别`
  else if (facts && value.sex !== facts.sex) {
    errors[`${prefix}sex`] = "性别与身份证信息不一致"
  }
  if (!isDateInput(value.date_of_birth) || value.date_of_birth > today) {
    errors[`${prefix}date_of_birth`] = "请填写有效且不晚于今天的出生日期"
  } else if (facts && value.date_of_birth !== facts.date_of_birth) {
    errors[`${prefix}date_of_birth`] = "出生日期与身份证信息不一致"
  } else if (guardian && !isAssistedAdult(value.date_of_birth, today)) {
    errors[`${prefix}date_of_birth`] = "监护人必须年满 18 周岁"
  }
  if (!/^1[3-9]\d{9}$/.test(value.phone.trim())) errors[`${prefix}phone`] = `请填写有效的${label}手机号`
  const email = value.email?.trim() ?? ""
  if (email && !z.email().safeParse(email).success) errors[`${prefix}email`] = "邮箱格式不正确"
}

function Field({ id, label, error, children }: { id: string; label: string; error?: string; children: ReactNode }) {
  return <div className="formField">
    <label htmlFor={id}>{label}</label>
    {children}
    {error ? <small className="fieldError" id={`${id}-error`}>{error}</small> : null}
  </div>
}

export function AssistedPersonForm(props: AssistedPersonFormProps) {
  const { initialValue, submitting, coachOptions = [], teamOptions = [], readOnly = false, identityOnlyKind, canEditActivationEmail = true } = props
  const management = props.allowStaff && !identityOnlyKind ? props.managementIdentity : undefined
  const [draft, setDraft] = useState(() => {
    const value = initialDraft(initialValue)
    return identityOnlyKind ? { ...value, kind: identityOnlyKind, coach_person_ids: [], team_id: null } : management ? { ...value, kind: identityKind(management.value), team_id: management.value.player_team_id } : value
  })
  const [errors, setErrors] = useState<Errors>({})
  const [pending, setPending] = useState(false)
  const [submitError, setSubmitError] = useState(false)
  const inFlight = useRef(false)
  const id = useId()
  const busy = pending || submitting
  const unavailableCoachIds = [...new Set(draft.coach_person_ids)].filter((coachId) =>
    !coachOptions.some((option) => option.id === coachId && !option.disabled))

  function update<K extends keyof PersonDraft>(key: K, value: PersonDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }))
  }

  function guardianUpdate(index: number, key: keyof GuardianDraft, value: string | boolean) {
    setDraft((current) => ({ ...current, guardians: current.guardians.map((guardian, position) =>
      position === index ? { ...guardian, [key]: value } : guardian) }))
  }

  function identityChange(value: PersonDraft | GuardianDraft, patch: Partial<PersonDraft & GuardianDraft>, index?: number) {
    const next = { ...value, ...patch }
    if (patch.document_type !== undefined) Object.assign(next, { identity_number: "", sex: "", date_of_birth: "", ...(value.document_issuing_country !== undefined ? { document_issuing_country: null } : {}) })
    else if (patch.identity_number !== undefined && (next.document_type ?? "cn_id") === "cn_id") Object.assign(next, cnDocumentFacts(patch.identity_number) ?? { sex: "", date_of_birth: "" })
    if (index === undefined) setDraft(next as PersonDraft)
    else setDraft((current) => ({ ...current, guardians: current.guardians.map((guardian, position) => position === index ? next as GuardianDraft : guardian) }))
  }

  function personFields(value: PersonDraft | GuardianDraft, index?: number) {
    const prefix = index === undefined ? "" : `guardians.${index}.`
    const guardian = index !== undefined
    const labels = { full_name: "姓名", identity_number: documentNumberLabel(value.document_type), date_of_birth: "出生日期", phone: "手机号", email: "邮箱" }
    return <div className="formGrid">
      <Field id={`${id}-${prefix}document_type`} label={`${guardian ? "监护人" : ""}证件类型`}>
        <select id={`${id}-${prefix}document_type`} value={value.document_type ?? "cn_id"} onChange={(event) => identityChange(value, { document_type: event.target.value as IdentityDocumentType }, index)}>
          {identityDocumentTypes.map((type) => <option key={type} value={type}>{documentTypeLabel(type)}</option>)}
        </select>
      </Field>
      {(Object.keys(labels) as Array<keyof typeof labels>).map((key) => {
        const fieldId = `${id}-${prefix}${key}`
        return <Field key={key} id={fieldId} label={`${guardian ? "监护人" : ""}${labels[key]}`} error={errors[`${prefix}${key}`]}>
          <input id={fieldId} type={key === "date_of_birth" ? "date" : key === "email" ? "email" : key === "phone" ? "tel" : "text"}
            autoComplete="off" value={!guardian && key === "email" && !canEditActivationEmail ? "" : value[key] ?? ""} maxLength={key === "full_name" ? 80 : key === "identity_number" ? 24 : undefined}
            max={key === "date_of_birth" ? todayDateInput() : undefined}
            disabled={!guardian && key === "email" && !canEditActivationEmail}
            aria-invalid={Boolean(errors[`${prefix}${key}`])} aria-describedby={errors[`${prefix}${key}`] ? `${fieldId}-error` : undefined}
            onChange={(event) => {
              if (!guardian && key === "email" && !canEditActivationEmail) return
              if (key === "identity_number") { identityChange(value, { identity_number: event.target.value }, index); return }
              if (index === undefined) update(key, event.target.value)
              else guardianUpdate(index, key, event.target.value)
            }} />
        </Field>
      })}
      <Field id={`${id}-${prefix}sex`} label={`${guardian ? "监护人" : ""}性别`} error={errors[`${prefix}sex`]}>
        <select id={`${id}-${prefix}sex`} value={value.sex} aria-invalid={Boolean(errors[`${prefix}sex`])}
          aria-describedby={errors[`${prefix}sex`] ? `${id}-${prefix}sex-error` : undefined}
          onChange={(event) => index === undefined ? update("sex", event.target.value as PersonDraft["sex"]) : guardianUpdate(index, "sex", event.target.value)}>
          <option value="">请选择</option><option value="male">男</option><option value="female">女</option>
        </select>
      </Field>
      <p className="assistedFormNotice">{guardian ? "监护人" : ""}{birthdayAgeText(value.date_of_birth)}{documentDemographicNotice(value.document_type) ? `；${documentDemographicNotice(value.document_type)}` : ""}</p>
    </div>
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy || readOnly || inFlight.current) return
    const nextErrors: Errors = {}
    if (management) Object.assign(nextErrors, identityValidation(management.value, management.context))
    const today = todayDateInput()
    validatePerson(canEditActivationEmail ? draft : { ...draft, email: null }, "", today, nextErrors)
    if (isDateInput(draft.date_of_birth) && !isAssistedAdult(draft.date_of_birth, today) && !draft.guardians.length) {
      nextErrors.guardians = "未成年人至少需要一名成年监护人"
    }
    draft.guardians.forEach((guardian, index) => {
      const prefix = `guardians.${index}.`
      validatePerson(guardian, prefix, today, nextErrors)
      if (!guardian.relationship.trim()) nextErrors[`${prefix}relationship`] = "请填写与本人关系"
      else if (guardian.relationship.trim().length < 2) nextErrors[`${prefix}relationship`] = "关系至少需要 2 个字"
      else if (guardian.relationship.trim().length > 40) nextErrors[`${prefix}relationship`] = "关系不能超过 40 个字"
      if (guardian.email?.trim() && guardian.email.trim().toLowerCase() === draft.email?.trim().toLowerCase()) {
        nextErrors[`${prefix}email`] = "监护人邮箱不能与本人邮箱相同"
      }
    })
    if (!identityOnlyKind && (draft.kind === "student" || draft.kind === "both") && (!draft.coach_person_ids.length || draft.coach_person_ids.some((coachId) =>
      !coachOptions.some((option) => option.id === coachId && !option.disabled)))) {
      nextErrors.coach_person_ids = "请至少选择一名可负责的教练"
    }
    if (!identityOnlyKind && (draft.kind === "player" || draft.kind === "both") && !teamOptions.some((option) => option.id === draft.team_id && !option.disabled)) {
      nextErrors.team_id = "请选择可加入的队伍"
    }
    setErrors(nextErrors)
    setSubmitError(false)
    if (Object.keys(nextErrors).length) return
    const input: Omit<IntakeInput, "kind"> & { kind: PendingPersonKind } = {
      ...draft, sex: draft.sex as IntakeInput["sex"], full_name: draft.full_name.trim(),
      identity_number: normalizeIdentityDocument(draft.identity_number, draft.document_type)!, phone: draft.phone.trim(), email: canEditActivationEmail ? draft.email?.trim().toLowerCase() || null : null,
      coach_person_ids: draft.kind === "student" || draft.kind === "both" ? [...new Set(draft.coach_person_ids)] : [],
      team_id: draft.kind === "player" || draft.kind === "both" ? draft.team_id : null,
      guardians: draft.guardians.map((guardian) => ({
        ...guardian, sex: guardian.sex as AssistedGuardianInput["sex"], full_name: guardian.full_name.trim(),
        identity_number: normalizeIdentityDocument(guardian.identity_number, guardian.document_type)!, phone: guardian.phone.trim(),
        email: guardian.email?.trim() || null, relationship: guardian.relationship.trim(), is_primary: guardian.is_primary ?? false,
      })),
    }
    inFlight.current = true
    setPending(true)
    try {
      if (props.allowStaff) await props.onSubmit(input)
      else if (input.kind !== "staff") await props.onSubmit({ ...input, kind: input.kind })
      else throw new Error("staff_not_allowed")
    }
    catch { setSubmitError(true) }
    finally { inFlight.current = false; setPending(false) }
  }

  return <form className="personFormCard assistedPersonForm" onSubmit={submit} noValidate autoComplete="off" aria-busy={busy}>
    <p className="assistedFormNotice">{identityOnlyKind ? "资料仅保留在当前页面内存中；更正由服务器校验管理权限并记录，不代表监护授权。" : "资料仅保留在当前页面内存中；本地检查不代替服务器校验。提交后需按权限审核。"}</p>
    <p className="assistedFormNotice">邮箱可后续补充；未激活账号不影响已审核人员办理住宿。</p>
    {!canEditActivationEmail ? <p className="assistedFormNotice">激活邮箱受保护，此处不会更改或清空。需要解除认领时，请管理员在认领审核中撤销邮箱绑定；已有账号请使用原联系方式验证变更流程。</p> : null}
    <fieldset className="assistedFormBody" disabled={busy || readOnly}>
      <legend>{identityOnlyKind ? "重新完整填写本人及监护资料" : "协助建立人员档案"}</legend>
      {!identityOnlyKind && !management ? <Field id={`${id}-kind`} label="人员身份">
        <select id={`${id}-kind`} value={draft.kind} onChange={(event) => {
          const kind = event.target.value as PendingPersonKind
          update("kind", kind)
        }}>
          <option value="student">学员</option><option value="player">队员</option><option value="both">学员兼队员</option>
          {props.allowStaff ? <option value="staff">其他工作人员</option> : null}
        </select>
      </Field> : null}
      <section className="formSection" aria-label="本人资料">{personFields(draft)}</section>
      {management ? <IntakeIdentitySection {...management} mode="strict" coachPersonIds={draft.coach_person_ids} coachOptions={coachOptions} teamOptions={teamOptions} disabled={busy || readOnly} errors={errors} onChange={(identity, coach_person_ids) => {
        management.onChange(identity)
        setDraft(current => ({ ...current, kind: identityKind(identity), coach_person_ids, team_id: identity.player_team_id }))
        setErrors({})
      }} /> : null}
      {!identityOnlyKind && !management && (draft.kind === "student" || draft.kind === "both") ? <fieldset className="assistedAssignments" aria-describedby={errors.coach_person_ids ? `${id}-coaches-error` : undefined}>
        <legend>负责教练</legend>
        {coachOptions.length ? coachOptions.map((option) => <label key={option.id}>
          <input type="checkbox" checked={draft.coach_person_ids.includes(option.id)} disabled={option.disabled}
            onChange={(event) => update("coach_person_ids", event.target.checked
              ? [...draft.coach_person_ids, option.id] : draft.coach_person_ids.filter((coachId) => coachId !== option.id))} />{option.label}
        </label>) : <p>暂无可选择的教练，请联系有权限的管理员。</p>}
        {unavailableCoachIds.length ? <div>
          <p className="assistedFormNotice">有已选教练现已不可用，请移除后选择可负责的教练；其余草稿不会清空。</p>
          {unavailableCoachIds.map((coachId, index) => <button key={coachId} type="button" className="secondaryButton"
            disabled={busy || readOnly} onClick={() => {
              if (busy || readOnly) return
              setDraft((current) => ({ ...current, coach_person_ids: current.coach_person_ids.filter((selectedId) => selectedId !== coachId) }))
            }}>移除不可用教练 {index + 1}</button>)}
        </div> : null}
        {errors.coach_person_ids ? <small className="fieldError" id={`${id}-coaches-error`}>{errors.coach_person_ids}</small> : null}
      </fieldset> : null}
      {!identityOnlyKind && !management && (draft.kind === "player" || draft.kind === "both") ? <Field id={`${id}-team`} label="所属队伍" error={errors.team_id}>
        <select id={`${id}-team`} value={draft.team_id ?? ""} aria-invalid={Boolean(errors.team_id)}
          aria-describedby={errors.team_id ? `${id}-team-error` : undefined} onChange={(event) => update("team_id", event.target.value || null)}>
          <option value="">请选择队伍</option>
          {teamOptions.map((option) => <option key={option.id} value={option.id} disabled={option.disabled}>{option.label}</option>)}
        </select>
      </Field> : null}
      <section className="formSection" aria-label="监护资料">
        <h2>监护人</h2><p className="assistedFormNotice">未成年人至少填写一名成年监护人。监护人邮箱可不填；资料审核不代表监护验证或家庭查看授权。</p>
        {errors.guardians ? <p className="fieldError" role="alert">{errors.guardians}</p> : null}
        {draft.guardians.map((guardian, index) => <fieldset key={index} className="assistedGuardian">
          <legend>监护人 {index + 1}</legend>
          {personFields(guardian, index)}
          <Field id={`${id}-guardian-${index}-relationship`} label="与本人关系" error={errors[`guardians.${index}.relationship`]}>
            <input id={`${id}-guardian-${index}-relationship`} value={guardian.relationship} maxLength={40}
              aria-invalid={Boolean(errors[`guardians.${index}.relationship`])}
              aria-describedby={errors[`guardians.${index}.relationship`] ? `${id}-guardian-${index}-relationship-error` : undefined}
              onChange={(event) => guardianUpdate(index, "relationship", event.target.value)} />
          </Field>
          <label className="assistedCheckbox"><input type="checkbox" checked={guardian.is_primary ?? false}
            onChange={(event) => guardianUpdate(index, "is_primary", event.target.checked)} />主要监护人</label>
          <button type="button" className="secondaryButton" onClick={() => update("guardians", draft.guardians.filter((_, position) => position !== index))}>移除监护人 {index + 1}</button>
        </fieldset>)}
        <button type="button" className="secondaryButton" onClick={() => update("guardians", [...draft.guardians, {
          full_name: "", identity_number: "", sex: "", date_of_birth: "", phone: "", email: null,
          relationship: "", is_primary: draft.guardians.length === 0,
        }])}>添加监护人</button>
      </section>
    </fieldset>
    {props.allowStaff && management ? props.submissionConfirmation : null}
    {submitError ? <p className="formSubmitError" role="alert">提交未成功，请检查服务器提示或稍后重试；当前草稿已保留。</p> : null}
    <div className="personFormActions"><button className="primaryButton" type="submit" disabled={busy || readOnly || Boolean(props.allowStaff && props.submitBlocked)}>{busy ? "正在提交…" : identityOnlyKind ? "提交更正" : "提交申请"}</button></div>
    {busy ? <p role="status">正在提交，请勿重复操作。</p> : null}
  </form>
}
