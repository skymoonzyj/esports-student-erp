import { useId } from "react"
import type { AssistedGuardianInput } from "../assistedPeople/types"
import { todayDateInput } from "../shared/date"
import type { ClaimCompletionInput } from "./types"
import { birthdayAgeText, documentDemographicNotice, documentNumberLabel, documentTypeLabel, identityDocumentTypes } from "../assistedPeople/documentFacts"
import type { IdentityDocumentType } from "../assistedPeople/documentFacts"

type GuardianDraft = Omit<AssistedGuardianInput, "sex"> & { sex: "male" | "female" | "" }
export interface ClaimCompletionDraft {
  phone: string | null
  sex: "male" | "female" | null
  date_of_birth: string | null
  issuing_country: string | null
  guardians: GuardianDraft[]
}
export const emptyClaimCompletion = (): ClaimCompletionDraft => ({ phone: null, sex: null, date_of_birth: null, issuing_country: null, guardians: [] })

export function toClaimCompletion(value: ClaimCompletionDraft): ClaimCompletionInput | null {
  if (!value.phone && !value.sex && !value.date_of_birth && !value.issuing_country && !value.guardians.length) return null
  return { ...value, guardians: value.guardians.map((guardian) => {
    if (guardian.sex !== "male" && guardian.sex !== "female") throw new Error("请填写监护人性别")
    return { ...guardian, sex: guardian.sex }
  }) }
}

export function ClaimCompletionFields({ value, onChange, disabled = false }: {
  value: ClaimCompletionDraft; onChange: (value: ClaimCompletionDraft) => void; disabled?: boolean
}) {
  const id = useId()
  const change = (patch: Partial<ClaimCompletionDraft>) => onChange({ ...value, ...patch })
  const guardianChange = (index: number, patch: Partial<GuardianDraft>) => change({ guardians: value.guardians.map((item, position) => position === index ? { ...item, ...patch } : item) })
  return <fieldset className="registrationFieldset claimCompletionFields" disabled={disabled}>
    <legend>本人补充资料（选填）</legend>
    <p>请只填写本人确认的事实，不清楚可留空。此处不查询或显示已有档案缺项；监护资料不等于监护授权。</p>
    <div className="registrationFieldGroup"><label htmlFor={`${id}-phone`}>联系电话（可暂缺）</label>
      <input id={`${id}-phone`} type="tel" autoComplete="off" maxLength={80} value={value.phone ?? ""} onChange={(event) => change({ phone: event.target.value || null })} /></div>
    <div className="registrationFieldGroup"><label htmlFor={`${id}-sex`}>性别（可暂缺）</label>
      <select id={`${id}-sex`} value={value.sex ?? ""} onChange={(event) => change({ sex: event.target.value as ClaimCompletionDraft["sex"] || null })}>
        <option value="">未填写</option><option value="male">男</option><option value="female">女</option></select></div>
    <div className="registrationFieldGroup"><label htmlFor={`${id}-birth`}>出生日期（可暂缺）</label>
      <input id={`${id}-birth`} type="date" min="1900-01-01" max={todayDateInput()} autoComplete="off" value={value.date_of_birth ?? ""} onChange={(event) => change({ date_of_birth: event.target.value || null })} />
      <small>四位年份，格式 YYYY-MM-DD，不含时间。</small></div>
    <p>{birthdayAgeText(value.date_of_birth)}</p>
    {value.guardians.map((guardian, index) => <fieldset className="registrationFieldset claimGuardianFields" key={index}>
      <legend>监护人 {index + 1}</legend>
      <div className="registrationFieldGroup"><label htmlFor={`${id}-${index}-document`}>{`监护人${index + 1}证件类型`}</label>
        <select id={`${id}-${index}-document`} value={guardian.document_type ?? "cn_id"} onChange={(event) => guardianChange(index, { document_type: event.target.value as IdentityDocumentType, identity_number: "", date_of_birth: "", sex: "", ...(guardian.document_issuing_country !== undefined ? { document_issuing_country: null } : {}) })}>{identityDocumentTypes.map((type) => <option key={type} value={type}>{documentTypeLabel(type)}</option>)}</select></div>
      {([["full_name", "姓名"], ["identity_number", documentNumberLabel(guardian.document_type)], ["phone", "手机号"], ["email", "邮箱（可选）"], ["relationship", "与本人关系"], ["date_of_birth", "出生日期"]] as const).map(([key, label]) => <div className="registrationFieldGroup" key={key}>
        <label htmlFor={`${id}-${index}-${key}`}>{`监护人${index + 1}${label}`}</label>
        <input id={`${id}-${index}-${key}`} type={key === "date_of_birth" ? "date" : key === "email" ? "email" : key === "phone" ? "tel" : "text"}
          min={key === "date_of_birth" ? "1900-01-01" : undefined} max={key === "date_of_birth" ? todayDateInput() : undefined}
          maxLength={key === "full_name" ? 80 : key === "relationship" ? 40 : key === "identity_number" ? guardian.document_type === "hk_id" ? 24 : 18 : undefined}
          minLength={key === "relationship" ? 2 : undefined} autoComplete="off" required={key !== "email"}
          value={guardian[key] ?? ""} onChange={(event) => guardianChange(index, { [key]: key === "email" ? event.target.value || null : event.target.value })} />
      </div>)}
      <p>监护人{birthdayAgeText(guardian.date_of_birth)}{documentDemographicNotice(guardian.document_type) ? `；${documentDemographicNotice(guardian.document_type)}` : ""}</p>
      <div className="registrationFieldGroup"><label htmlFor={`${id}-${index}-sex`}>{`监护人${index + 1}性别`}</label>
        <select id={`${id}-${index}-sex`} value={guardian.sex} required onChange={(event) => guardianChange(index, { sex: event.target.value as GuardianDraft["sex"] })}>
          <option value="">请选择</option><option value="male">男</option><option value="female">女</option></select></div>
      <label><input type="checkbox" checked={guardian.is_primary ?? false} onChange={(event) => guardianChange(index, { is_primary: event.target.checked })} />{`监护人${index + 1}为主要监护人`}</label>
      <button type="button" className="secondaryButton" onClick={() => change({ guardians: value.guardians.filter((_, position) => position !== index) })}>{`移除监护人 ${index + 1}`}</button>
    </fieldset>)}
    <button type="button" className="secondaryButton" disabled={value.guardians.length >= 10} onClick={() => change({ guardians: [...value.guardians,
      { full_name: "", identity_number: "", phone: "", email: null, relationship: "", date_of_birth: "", sex: "", is_primary: false }] })}>添加监护人（最多 10 位）</button>
  </fieldset>
}
