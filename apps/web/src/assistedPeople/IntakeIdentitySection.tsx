import { useEffect, useId, useRef, useState } from "react"
import type { ManagementIdentityContext } from "./api"
import type { AssistedAssignmentOption } from "./AssistedPersonForm"
import { extraIdentityKeys, identityNames, previewIdentityChange, selectedIdentities, staffIdentities, staffRoleIdentity, normalizeIdentityInput } from "./intakeIdentity"
import type { IntakeIdentityDraft, RemovedAssignment } from "./intakeIdentity"
import type { IdentityKey, ManagedIdentityInput, TeamStaffRole } from "./types"
import { identityKeys } from "./types"
import "./intakeIdentity.css"

export interface ManagementIdentityControls {
  value: IntakeIdentityDraft
  onChange: (value: IntakeIdentityDraft) => void
  context: ManagementIdentityContext
}
interface Props extends Omit<ManagementIdentityControls, "onChange"> {
  onChange: (value: IntakeIdentityDraft, coachPersonIds: string[]) => void
  coachPersonIds: string[]
  coachOptions: readonly AssistedAssignmentOption[]
  teamOptions: readonly AssistedAssignmentOption[]
  mode: "strict" | "pending"
  disabled?: boolean
  errors?: Record<string, string>
}
export function identityValidation(value: IntakeIdentityDraft, context: ManagementIdentityContext): Record<string, string> {
  const errors: Record<string, string> = {}
  try { normalizeIdentityInput(value) } catch (error) { errors.identity = error instanceof Error ? error.message : "请核对人员身份" }
  if (extraIdentityKeys(value).some(key => !context.role_options.some(option => option.key === key && (option.assignable || option.requestable)))) errors.identity = "已选身份现已不可用，请明确移除或更正后提交"
  for (const role of Object.keys(staffRoleIdentity) as TeamStaffRole[]) {
    const ids = value.staff_team_ids[role]
    if (ids.length && (!context.can_assign_staff_teams || ids.some(id => !context.staff_team_options.some(option => option.id === id)))) errors[`staff_${role}`] = "已选队伍或分配权限已失效，请明确移除后重新核实"
  }
  return errors
}
export function IntakeIdentitySection({ value, onChange, context, coachPersonIds, coachOptions, teamOptions, mode, disabled = false, errors = {} }: Props) {
  const id = useId()
  const [preview, setPreview] = useState<ReturnType<typeof previewIdentityChange> | null>(null)
  const primary = useRef<HTMLSelectElement>(null)
  const hadPreview = useRef(false)
  useEffect(() => {
    if (hadPreview.current && !preview) primary.current?.focus()
    hadPreview.current = Boolean(preview)
  }, [preview])
  const selected = selectedIdentities(value)
  const staff = selected.filter(key => staffIdentities.includes(key))
  const catalog = context.role_options.filter(option => identityKeys.includes(option.key as IdentityKey))
  const invalid = extraIdentityKeys(value).filter(key => !catalog.some(option => option.key === key && (option.assignable || option.requestable)))
  const pendingRoles = extraIdentityKeys(value).filter(key => catalog.some(option => option.key === key && !option.assignable && option.requestable))
  function assignable(key: IdentityKey, asPrimary: boolean) {
    const prospective = { primary_identity: asPrimary ? key : value.primary_identity, concurrent_identities: asPrimary ? value.concurrent_identities.filter(item => item !== key) : [...value.concurrent_identities, key] }
    return !extraIdentityKeys(prospective).includes(key) || catalog.some(option => option.key === key && (option.assignable || option.requestable))
  }
  const error = errors.identity ?? (invalid.length ? "已选身份现已不可用，请明确移除或更正后提交" : undefined)
  function selection(next: Omit<ManagedIdentityInput, "primary_identity"> & { primary_identity: IdentityKey | null }) {
    if (disabled) return
    const nextStaff = selectedIdentities(next).filter(key => staffIdentities.includes(key))
    next.primary_staff_identity = next.primary_identity && staffIdentities.includes(next.primary_identity) ? null : next.primary_staff_identity && nextStaff.includes(next.primary_staff_identity) ? next.primary_staff_identity : null
    const result = previewIdentityChange(value, next, coachPersonIds)
    if (result.removedAssignments.length) setPreview(result)
    else onChange(result.next, result.nextCoachPersonIds)
  }
  function toggle(key: IdentityKey, checked: boolean) {
    if (!value.primary_identity) return
    selection({ primary_identity: value.primary_identity, concurrent_identities: checked ? [...value.concurrent_identities, key] : value.concurrent_identities.filter(item => item !== key), primary_staff_identity: value.primary_staff_identity })
  }
  function assignmentName(kind: RemovedAssignment["kind"], assignmentId: string) {
    if (kind === "coach") return coachOptions.find(option => option.id === assignmentId)?.label ?? "不可用负责教练"
    if (kind === "player_team") return teamOptions.find(option => option.id === assignmentId)?.label ?? "不可用队员队伍"
    const team = context.staff_team_options.find(option => option.id === assignmentId)
    return team ? `${team.project_name} / ${team.name}` : "不可用工作人员队伍"
  }
  function closePreview() { setPreview(null) }
  return <fieldset className="intakeIdentity" disabled={disabled}>
    <legend>人员身份</legend>
    {pendingRoles.length ? <p>所选角色待超级管理员批准，不影响人员建档及基础账号激活；待批角色和相关队伍访问暂不生效。</p> : null}
    <div className="intakeIdentityBody" inert={Boolean(preview)}>
    <div className="formField"><label htmlFor={`${id}-primary`}>主要身份</label>
      <select ref={primary} id={`${id}-primary`} value={value.primary_identity ?? ""} aria-invalid={Boolean(error)} aria-describedby={error ? `${id}-error` : undefined} onChange={event => {
        const key = event.target.value as IdentityKey
        if (key) selection({ primary_identity: key, concurrent_identities: value.concurrent_identities.filter(item => item !== key), primary_staff_identity: value.primary_staff_identity })
      }}><option value="">请选择主要身份</option>{catalog.map(option => <option key={option.key} value={option.key} disabled={!assignable(option.key as IdentityKey, true)}>{identityNames[option.key as IdentityKey]}{!assignable(option.key as IdentityKey, true) ? `（${option.reason ?? "不可授予"}）` : ""}</option>)}</select>
      {error ? <p className="fieldError" id={`${id}-error`} role="alert">{error}</p> : null}
    </div>
    <p className="intakeIdentityTags" role="status">{selected.length ? selected.map(key => identityNames[key]).join("、") : "尚未选择人员身份"}</p>
    <details open={value.concurrent_identities.length > 0 || undefined}><summary>兼任身份</summary><div className="intakeIdentityChoices">
      {catalog.filter(option => option.key !== value.primary_identity).map(option => <div key={option.key}>
        <label><input type="checkbox" disabled={!assignable(option.key as IdentityKey, false) || !value.primary_identity} checked={value.concurrent_identities.includes(option.key as IdentityKey)} aria-describedby={!assignable(option.key as IdentityKey, false) ? `${id}-reason-${option.key}` : undefined} onChange={event => toggle(option.key as IdentityKey, event.target.checked)} />{identityNames[option.key as IdentityKey]}</label>
        {!assignable(option.key as IdentityKey, false) ? <small id={`${id}-reason-${option.key}`}>{option.reason ?? "当前不可授予此身份"}</small> : null}
      </div>)}
    </div></details>
    {invalid.length ? <div>{invalid.map(key => <button type="button" key={key} onClick={() => {
      if (value.primary_identity === key) return selection({ primary_identity: null, concurrent_identities: [...value.concurrent_identities], primary_staff_identity: value.primary_staff_identity })
      toggle(key, false)
    }}>移除不可用身份：{identityNames[key]}</button>)}</div> : null}
    {!value.primary_identity && value.concurrent_identities.length ? <p>旧草稿的身份、队伍及负责人已保留，请明确确认主要身份。</p> : null}
    {value.primary_identity && !staffIdentities.includes(value.primary_identity) && staff.length > 1 ? <div className="formField"><label htmlFor={`${id}-staff-primary`}>主要工作人员岗位</label><select id={`${id}-staff-primary`} value={value.primary_staff_identity ?? ""} onChange={event => onChange({ ...value, primary_staff_identity: event.target.value as IdentityKey || null }, coachPersonIds)}><option value="">请明确选择主要岗位</option>{staff.map(key => <option key={key} value={key}>{identityNames[key]}</option>)}</select></div> : null}
    {selected.includes("student") ? <fieldset className="assistedAssignments" aria-describedby={errors.coach_person_ids ? `${id}-coach-error` : undefined}><legend>负责教练{mode === "pending" ? "（可暂缺）" : ""}</legend>
      {coachOptions.map(option => <label key={option.id}><input type="checkbox" checked={coachPersonIds.includes(option.id)} disabled={option.disabled} aria-invalid={Boolean(errors.coach_person_ids)} aria-describedby={errors.coach_person_ids ? `${id}-coach-error` : undefined} onChange={event => onChange(value, event.target.checked ? [...coachPersonIds, option.id] : coachPersonIds.filter(item => item !== option.id))} />{option.label}</label>)}
      {coachPersonIds.filter(item => !coachOptions.some(option => option.id === item && !option.disabled)).map(item => <button type="button" key={item} onClick={() => onChange(value, coachPersonIds.filter(key => key !== item))}>移除不可用负责教练</button>)}
      {errors.coach_person_ids ? <p className="fieldError" role="alert" id={`${id}-coach-error`}>{errors.coach_person_ids}</p> : null}
    </fieldset> : null}
    {selected.includes("team_member") ? <div className="formField"><label htmlFor={`${id}-player-team`}>队员所属队伍{mode === "pending" ? "（可暂缺）" : ""}</label><select id={`${id}-player-team`} value={value.player_team_id ?? ""} aria-invalid={Boolean(errors.team_id)} aria-describedby={errors.team_id ? `${id}-team-error` : undefined} onChange={event => onChange({ ...value, player_team_id: event.target.value || null }, coachPersonIds)}><option value="">{mode === "pending" ? "暂不分配" : "请选择队伍"}</option>{teamOptions.map(option => <option key={option.id} value={option.id} disabled={option.disabled}>{option.label}</option>)}</select>{errors.team_id ? <p id={`${id}-team-error`} className="fieldError">{errors.team_id}</p> : null}</div> : null}
    {(Object.keys(staffRoleIdentity) as TeamStaffRole[]).filter(role => selected.includes(staffRoleIdentity[role])).map(role => <fieldset key={role} className="assistedAssignments" aria-describedby={!context.can_assign_staff_teams ? `${id}-staff-denied` : errors[`staff_${role}`] ? `${id}-staff-${role}-error` : undefined}>
      <legend>{identityNames[staffRoleIdentity[role]]}负责队伍（可暂不分配）</legend>
      {context.staff_team_options.map(option => <label key={option.id}><input type="checkbox" disabled={!context.can_assign_staff_teams} checked={value.staff_team_ids[role].includes(option.id)} onChange={event => onChange({ ...value, staff_team_ids: { ...value.staff_team_ids, [role]: event.target.checked ? [...value.staff_team_ids[role], option.id] : value.staff_team_ids[role].filter(item => item !== option.id) } }, coachPersonIds)} />{option.project_name} / {option.name}</label>)}
      {value.staff_team_ids[role].filter(item => !context.can_assign_staff_teams || !context.staff_team_options.some(option => option.id === item)).map(item => <button type="button" key={item} onClick={() => onChange({ ...value, staff_team_ids: { ...value.staff_team_ids, [role]: value.staff_team_ids[role].filter(key => key !== item) } }, coachPersonIds)}>移除不可用{identityNames[staffRoleIdentity[role]]}队伍</button>)}
      {errors[`staff_${role}`] ? <p id={`${id}-staff-${role}-error`} role="alert">{errors[`staff_${role}`]}</p> : null}
    </fieldset>)}
    {!context.can_assign_staff_teams && selected.some(key => Object.values(staffRoleIdentity).includes(key)) ? <p id={`${id}-staff-denied`}>{context.staff_team_reason ?? "队伍须由有权人员分配；可以暂不分队建档。"}</p> : null}
    <p>账号激活后才可登录；队内岗位仅授予本队只读范围。选择教练不自动建立教学项目或班级关系。</p>
    {selected.includes("finance") ? <p>财务身份仅实际超级管理员可批准，激活时仍须复核。</p> : null}
    {selected.includes("guardian") ? <p>角色不会自动建立监护关系；监护查看仍需单独验证和授权。</p> : null}
    </div>
    {preview ? <div role="dialog" aria-modal="false" aria-labelledby={`${id}-confirm-title`} className="intakeIdentityConfirmation" onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); closePreview() } }}><h3 id={`${id}-confirm-title`}>更改身份将移除以下归属</h3><ul>{preview.removedAssignments.map(item => <li key={`${item.kind}:${item.staff_role}:${item.id}`}>{item.kind === "coach" ? "负责教练" : item.staff_role ? `${identityNames[staffRoleIdentity[item.staff_role]]}负责队伍` : "队员所属队伍"}：{assignmentName(item.kind, item.id)}</li>)}</ul><button type="button" autoFocus onClick={closePreview}>取消身份更改</button><button type="button" onClick={() => { onChange(preview.next, preview.nextCoachPersonIds); closePreview() }}>确认移除归属并更改身份</button></div> : null}
  </fieldset>
}
