import type { ControlRect } from '../contracts'

export interface TouchMobaControlsProps {
  readonly controls: readonly ControlRect[]
  readonly disabled?: boolean
}

/** Visual labels only; the Host's one TouchInputRouter owns every pointer and its timing. */
export function TouchMobaControls({ controls, disabled = false }: TouchMobaControlsProps) {
  const labels = { move: '移动', attack: '攻击', skill: '技能' } as const
  return <div aria-hidden="true" style={{ position: 'absolute', inset: 0, pointerEvents: 'none', opacity: disabled ? .45 : 1 }}>
    {controls.filter((control): control is ControlRect & { id: keyof typeof labels } => control.id in labels).map(control => <div key={control.id} style={{
      position: 'absolute', left: `${control.x * 100}%`, top: `${control.y * 100}%`, width: control.width, height: control.height,
      transform: 'translate(-50%, -50%)', borderRadius: '50%', boxSizing: 'border-box', border: '2px solid rgba(224,239,255,.7)',
      background: 'rgba(30,51,70,.45)', color: '#fff', display: 'grid', placeItems: 'center', fontSize: 14, userSelect: 'none', pointerEvents: 'none',
    }}>{labels[control.id]}</div>)}
  </div>
}

export default TouchMobaControls
