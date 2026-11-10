import { useId } from "react"
import type { ActivationRoleOptions } from "./api"

export function unavailableActivationRoles(options: ActivationRoleOptions, selected: readonly string[]) {
  return selected.filter(key => !options.items.some(item => item.key === key && item.assignable))
}

export function ActivationRolePicker({ options, selected, onChange, disabled = false }: {
  options: ActivationRoleOptions; selected: readonly string[]; onChange: (keys: string[]) => void; disabled?: boolean
}) {
  const id = useId()
  const unavailable = unavailableActivationRoles(options, selected)
  return <fieldset className="assistedAssignments" disabled={disabled}>
    <legend>账号激活角色（激活后生效）</legend>
    <p>建档身份与账号角色分开；学员/队员的最低本人角色按原规则保留。工作人员分类本身不授予权限。</p>
    <p>角色不会自动建立监护关系、教练负责关系或队伍归属；监护查看仍需单独授权。</p>
    {options.items.map(item => <div key={item.key}>
      <label><input type="checkbox" checked={selected.includes(item.key)} disabled={!item.assignable}
        aria-describedby={!item.assignable && item.reason ? `${id}-${item.key}` : undefined}
        onChange={event => onChange(event.target.checked ? [...selected, item.key] : selected.filter(key => key !== item.key))} />{item.name}</label>
      {!item.assignable && item.reason ? <p id={`${id}-${item.key}`}>{item.reason}</p> : null}
    </div>)}
    {unavailable.length ? <><p role="alert">已选角色现已不可用，请移除后重新核实；不会静默删减授权。</p>
      {unavailable.map((key, index) => <button type="button" key={key} onClick={() => onChange(selected.filter(item => item !== key))}>移除不可用角色 {index + 1}</button>)}</> : null}
  </fieldset>
}
