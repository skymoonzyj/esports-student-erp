import type { ControlRect, ControlSettings, SuiteId } from '../contracts'
import type { Rect } from '../engineRuntime/types'
import { normalizeSensitivity, type LayoutSafety, validateLayout } from './layout'

const controlLabels: Readonly<Record<ControlRect['id'], string>> = {
  move: '移动摇杆',
  fire: '开火按钮',
  attack: '攻击按钮',
  skill: '技能按钮',
}

export interface ControlSettingsPanelProps {
  readonly suite: SuiteId
  readonly value: ControlSettings
  readonly bounds: Rect
  readonly safety: LayoutSafety
  readonly disabled?: boolean
  readonly onChange: (value: ControlSettings) => void
}

export function ControlSettingsPanel({
  suite,
  value,
  bounds,
  safety,
  disabled = false,
  onChange,
}: ControlSettingsPanelProps) {
  const errors = validateLayout(value.controls, bounds, safety)

  const updateControl = (id: ControlRect['id'], patch: Partial<ControlRect>) => {
    onChange({
      ...value,
      controls: value.controls.map(control => control.id === id ? { ...control, ...patch } : control),
    })
  }

  return (
    <section aria-labelledby="control-settings-title">
      <h2 id="control-settings-title">操作设置</h2>
      <label htmlFor="assessment-sensitivity">灵敏度</label>
      <input
        id="assessment-sensitivity"
        aria-label="灵敏度"
        type="number"
        min="0.5"
        max="2"
        step="0.05"
        value={value.sensitivity}
        disabled={disabled}
        onChange={event => onChange({
          ...value,
          sensitivity: normalizeSensitivity(Number(event.currentTarget.value)),
        })}
      />

      {suite === 'pc-fps' && (
        <label>
          鼠标输入模式
          <select
            aria-label="鼠标输入模式"
            value={value.mouse_mode ?? 'relative'}
            disabled={disabled}
            onChange={event => onChange({
              ...value,
              mouse_mode: event.currentTarget.value === 'raw' ? 'raw' : 'relative',
            })}
          >
            <option value="relative">普通相对鼠标</option>
            <option value="raw">请求原始鼠标输入</option>
          </select>
        </label>
      )}

      {value.controls.map(control => {
        const label = controlLabels[control.id]
        const minimum = control.kind === 'joystick' ? 96 : 48
        const maximum = control.kind === 'joystick' ? 160 : 96
        return (
          <fieldset key={control.id}>
            <legend>{label}</legend>
            <label>
              {label}大小
              <input
                aria-label={`${label}大小`}
                type="number"
                min={minimum}
                max={maximum}
                step="1"
                value={control.width}
                disabled={disabled}
                onChange={event => {
                  const size = Math.min(maximum, Math.max(minimum, Number(event.currentTarget.value)))
                  updateControl(control.id, { width: size, height: size })
                }}
              />
            </label>
            <label>
              {label}横向位置
              <input
                aria-label={`${label}横向位置`}
                type="range"
                min="0"
                max="1"
                step="0.01"
                value={control.x}
                disabled={disabled}
                onChange={event => updateControl(control.id, {
                  x: Math.min(1, Math.max(0, Number(event.currentTarget.value))),
                })}
              />
            </label>
            <label>
              {label}纵向位置
              <input
                aria-label={`${label}纵向位置`}
                type="range"
                min="0"
                max="1"
                step="0.01"
                value={control.y}
                disabled={disabled}
                onChange={event => updateControl(control.id, {
                  y: Math.min(1, Math.max(0, Number(event.currentTarget.value))),
                })}
              />
            </label>
          </fieldset>
        )
      })}

      {errors.length > 0 && (
        <div role="alert">
          {errors.map(error => <p key={error}>{error}</p>)}
        </div>
      )}
    </section>
  )
}
