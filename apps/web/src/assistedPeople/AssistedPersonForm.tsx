import { useId, useRef, useState } from "react"
import type { FormEvent, ReactNode } from "react"
import { z } from "zod"

import { isDateInput, todayDateInput } from "../shared/date"
import type { AssistedGuardianInput, IntakeInput, PersonKind } from "./types"
import { isAssistedAdult } from "./calendarAge"
import "../people/people.css"
import "./assisted-people.css"

export interface AssistedAssignmentOption {
  id: string
  label: string
  disabled?: boolean
}

export interface AssistedPersonFormProps {
  /** Applied only on mount. Mount with the account ID as key when accounts change. */
  initialValue?: IntakeInput
  onSubmit: (input: IntakeInput) => Promise<void>
  submitting: boolean
  coachOptions?: readonly AssistedAssignmentOption[]
  teamOptions?: readonly AssistedAssignmentOption[]
  readOnly?: boolean
  /** Explicit full-facts correction mode: kind and affiliations are immutable. */
  identityOnlyKind?: PersonKind
}

type PersonDraft = Omit<IntakeInput, "sex" | "guardians"> & {
  sex: IntakeInput["sex"] | ""
  guardians: GuardianDraft[]
}
type GuardianDraft = Omit<AssistedGuardianInput, "sex"> & { sex: AssistedGuardianInput["sex"] | "" }
type Errors = Record<string, string>
const weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
const checks = "10X98765432"

function identityValid(value: string): boolean {
  if (!/^\d{17}[\dX]$/.test(value)) return false
  const birth = `${value.slice(6, 10)}-${value.slice(10, 12)}-${value.slice(12, 14)}`
  if (!isDateInput(birth)) return false
  const sum = [...value.slice(0, 17)].reduce((total, digit, index) => total + Number(digit) * weights[index], 0)
  return value[17] === checks[sum % 11]
}

function initialDraft(value?: IntakeInput): PersonDraft {
  return value ? {
    ...value,
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
  const validIdentity = identityValid(identity)
  if (!validIdentity) errors[`${prefix}identity_number`] = "请填写有效的 18 位身份证号码（含校验码）"
  if (!value.sex) errors[`${prefix}sex`] = `请选择${label}性别`
  else if (validIdentity && value.sex !== (Number(identity[16]) % 2 ? "male" : "female")) {
    errors[`${prefix}sex`] = "性别与身份证信息不一致"
  }
  if (!isDateInput(value.date_of_birth) || value.date_of_birth > today) {
    errors[`${prefix}date_of_birth`] = "请填写有效且不晚于今天的出生日期"
  } else if (validIdentity && value.date_of_birth !== `${identity.slice(6, 10)}-${identity.slice(10, 12)}-${identity.slice(12, 14)}`) {
    errors[`${prefix}date_of_birth`] = "出生日期与身份证信息不一致"
  } else if (guardian && !isAssistedAdult(value.date_of_birth, today)) {
    errors[`${prefix}date_of_birth`] = "监护人必须年满 18 周岁"
  }
  if (!/^1[3-9]\d{9}$/.test(value.phone.trim())) errors[`${prefix}phone`] = `请填写有效的${label}手机号`
  const email = value.email?.trim() ?? ""
  if (!guardian && !email) errors.email = "请填写本人独立邮箱"
  else if (email && !z.email().safeParse(email).success) errors[`${prefix}email`] = "邮箱格式不正确"
}

function Field({ id, label, error, children }: { id: string; label: string; error?: string; children: ReactNode }) {
  return <div className="formField">
    <label htmlFor={id}>{label}</label>
    {children}
    {error ? <small className="fieldError" id={`${id}-error`}>{error}</small> : null}
  </div>
}

export function AssistedPersonForm({
  initialValue, onSubmit, submitting, coachOptions = [], teamOptions = [], readOnly = false, identityOnlyKind,
}: AssistedPersonFormProps) {
  const [draft, setDraft] = useState(() => {
    const value = initialDraft(initialValue)
    return identityOnlyKind ? { ...value, kind: identityOnlyKind, coach_person_ids: [], team_id: null } : value
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

  function personFields(value: PersonDraft | GuardianDraft, index?: number) {
    const prefix = index === undefined ? "" : `guardians.${index}.`
    const guardian = index !== undefined
    const labels = { full_name: "姓名", identity_number: "身份证", date_of_birth: "出生日期", phone: "手机号", email: "邮箱" }
    return <div className="formGrid">
      {(Object.keys(labels) as Array<keyof typeof labels>).map((key) => {
        const fieldId = `${id}-${prefix}${key}`
        return <Field key={key} id={fieldId} label={`${guardian ? "监护人" : ""}${labels[key]}`} error={errors[`${prefix}${key}`]}>
          <input id={fieldId} type={key === "date_of_birth" ? "date" : key === "email" ? "email" : key === "phone" ? "tel" : "text"}
            autoComplete="off" value={value[key] ?? ""} maxLength={key === "full_name" ? 80 : undefined}
            aria-invalid={Boolean(errors[`${prefix}${key}`])} aria-describedby={errors[`${prefix}${key}`] ? `${fieldId}-error` : undefined}
            onChange={(event) => index === undefined ? update(key, event.target.value) : guardianUpdate(index, key, event.target.value)} />
        </Field>
      })}
      <Field id={`${id}-${prefix}sex`} label={`${guardian ? "监护人" : ""}性别`} error={errors[`${prefix}sex`]}>
        <select id={`${id}-${prefix}sex`} value={value.sex} aria-invalid={Boolean(errors[`${prefix}sex`])}
          aria-describedby={errors[`${prefix}sex`] ? `${id}-${prefix}sex-error` : undefined}
          onChange={(event) => index === undefined ? update("sex", event.target.value as PersonDraft["sex"]) : guardianUpdate(index, "sex", event.target.value)}>
          <option value="">请选择</option><option value="male">男</option><option value="female">女</option>
        </select>
      </Field>
    </div>
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (busy || readOnly || inFlight.current) return
    const nextErrors: Errors = {}
    const today = todayDateInput()
    validatePerson(draft, "", today, nextErrors)
    if (isDateInput(draft.date_of_birth) && !isAssistedAdult(draft.date_of_birth, today) && !draft.guardians.length) {
      nextErrors.guardians = "未成年人至少需要一名成年监护人"
    }
    draft.guardians.forEach((guardian, index) => {
      const prefix = `guardians.${index}.`
      validatePerson(guardian, prefix, today, nextErrors)
      if (!guardian.relationship.trim()) nextErrors[`${prefix}relationship`] = "请填写与本人关系"
      else if (guardian.relationship.trim().length < 2) nextErrors[`${prefix}relationship`] = "关系至少需要 2 个字"
      else if (guardian.relationship.trim().length > 40) nextErrors[`${prefix}relationship`] = "关系不能超过 40 个字"
      if (guardian.email?.trim() && guardian.email.trim().toLowerCase() === draft.email.trim().toLowerCase()) {
        nextErrors[`${prefix}email`] = "监护人邮箱不能与本人邮箱相同"
      }
    })
    if (!identityOnlyKind && draft.kind !== "player" && (!draft.coach_person_ids.length || draft.coach_person_ids.some((coachId) =>
      !coachOptions.some((option) => option.id === coachId && !option.disabled)))) {
      nextErrors.coach_person_ids = "请至少选择一名可负责的教练"
    }
    if (!identityOnlyKind && draft.kind !== "student" && !teamOptions.some((option) => option.id === draft.team_id && !option.disabled)) {
      nextErrors.team_id = "请选择可加入的队伍"
    }
    setErrors(nextErrors)
    setSubmitError(false)
    if (Object.keys(nextErrors).length) return
    const input: IntakeInput = {
      ...draft, sex: draft.sex as IntakeInput["sex"], full_name: draft.full_name.trim(),
      identity_number: draft.identity_number.trim().toUpperCase(), phone: draft.phone.trim(), email: draft.email.trim(),
      coach_person_ids: draft.kind === "player" ? [] : [...new Set(draft.coach_person_ids)],
      team_id: draft.kind === "student" ? null : draft.team_id,
      guardians: draft.guardians.map((guardian) => ({
        ...guardian, sex: guardian.sex as AssistedGuardianInput["sex"], full_name: guardian.full_name.trim(),
        identity_number: guardian.identity_number.trim().toUpperCase(), phone: guardian.phone.trim(),
        email: guardian.email?.trim() || null, relationship: guardian.relationship.trim(), is_primary: guardian.is_primary ?? false,
      })),
    }
    inFlight.current = true
    setPending(true)
    try { await onSubmit(input) }
    catch { setSubmitError(true) }
    finally { inFlight.current = false; setPending(false) }
  }

  return <form className="personFormCard assistedPersonForm" onSubmit={submit} noValidate autoComplete="off" aria-busy={busy}>
    <p className="assistedFormNotice">{identityOnlyKind ? "资料仅保留在当前页面内存中；更正由服务器校验管理权限并记录，不代表监护授权。" : "资料仅保留在当前页面内存中；本地检查不代替服务器校验。提交后需按权限审核。"}</p>
    <fieldset className="assistedFormBody" disabled={busy || readOnly}>
      <legend>{identityOnlyKind ? "重新完整填写本人及监护资料" : "协助建立人员档案"}</legend>
      {!identityOnlyKind ? <Field id={`${id}-kind`} label="人员身份">
        <select id={`${id}-kind`} value={draft.kind} onChange={(event) => update("kind", event.target.value as PersonKind)}>
          <option value="student">学员</option><option value="player">队员</option><option value="both">学员兼队员</option>
        </select>
      </Field> : null}
      <section className="formSection" aria-label="本人资料">{personFields(draft)}</section>
      {!identityOnlyKind && draft.kind !== "player" ? <fieldset className="assistedAssignments" aria-describedby={errors.coach_person_ids ? `${id}-coaches-error` : undefined}>
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
      {!identityOnlyKind && draft.kind !== "student" ? <Field id={`${id}-team`} label="所属队伍" error={errors.team_id}>
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
    {submitError ? <p className="formSubmitError" role="alert">提交未成功，请检查服务器提示或稍后重试；当前草稿已保留。</p> : null}
    <div className="personFormActions"><button className="primaryButton" type="submit" disabled={busy || readOnly}>{busy ? "正在提交…" : identityOnlyKind ? "提交更正" : "提交申请"}</button></div>
    {busy ? <p role="status">正在提交，请勿重复操作。</p> : null}
  </form>
}
