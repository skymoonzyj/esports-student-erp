import type { ControlRect } from '../contracts'

export interface TouchFpsControlsProps {
  readonly controls: readonly ControlRect[]
}

const labels: Readonly<Record<ControlRect['id'], string>> = {
  fire: '开火',
  move: '移动',
  attack: '攻击',
  skill: '技能',
}

export function TouchFpsControls({ controls }: TouchFpsControlsProps) {
  return (
    <div
      aria-hidden="true"
      style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}
    >
      {controls.map(control => (
        <span
          key={control.id}
          data-control-id={control.id}
          style={{
            position: 'absolute',
            left: `${control.x * 100}%`,
            top: `${control.y * 100}%`,
            width: control.width,
            height: control.height,
            transform: 'translate(-50%, -50%)',
            display: 'grid',
            placeItems: 'center',
            border: '2px solid rgb(255 255 255 / 75%)',
            borderRadius: '50%',
            color: '#fff',
            background: 'rgb(11 18 32 / 45%)',
            fontWeight: 700,
            userSelect: 'none',
          }}
        >
          {labels[control.id]}
        </span>
      ))}
    </div>
  )
}
