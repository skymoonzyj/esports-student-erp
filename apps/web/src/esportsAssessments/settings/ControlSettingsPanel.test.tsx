import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ControlSettingsPanel } from './ControlSettingsPanel'
import { defaultControlSettings } from './layout'

const bounds = { x: 0, y: 0, width: 640, height: 360 }

describe('ControlSettingsPanel', () => {
  it('normalizes sensitivity to the server range and step', () => {
    const onChange = vi.fn()
    render(
      <ControlSettingsPanel
        suite="mobile-fps"
        value={defaultControlSettings('mobile-fps')}
        bounds={bounds}
        safety={{ usable: bounds, reserved: [] }}
        onChange={onChange}
      />,
    )

    fireEvent.change(screen.getByLabelText('灵敏度'), { target: { value: '1.026' } })

    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ sensitivity: 1.05 }))
  })

  it('clamps button and joystick diameters to their server limits', () => {
    const onChange = vi.fn()
    render(
      <ControlSettingsPanel
        suite="mobile-moba"
        value={defaultControlSettings('mobile-moba')}
        bounds={bounds}
        safety={{ usable: bounds, reserved: [] }}
        onChange={onChange}
      />,
    )

    fireEvent.change(screen.getByLabelText('攻击按钮大小'), { target: { value: '120' } })
    expect(onChange.mock.calls.at(-1)?.[0].controls.find((item: { id: string }) => item.id === 'attack'))
      .toMatchObject({ width: 96, height: 96 })

    fireEvent.change(screen.getByLabelText('移动摇杆大小'), { target: { value: '40' } })
    expect(onChange.mock.calls.at(-1)?.[0].controls.find((item: { id: string }) => item.id === 'move'))
      .toMatchObject({ width: 96, height: 96 })
  })

  it('keeps raw and relative mouse modes explicit', () => {
    const onChange = vi.fn()
    render(
      <ControlSettingsPanel
        suite="pc-fps"
        value={defaultControlSettings('pc-fps')}
        bounds={{ x: 0, y: 0, width: 800, height: 450 }}
        safety={{ usable: { x: 0, y: 0, width: 800, height: 450 }, reserved: [] }}
        onChange={onChange}
      />,
    )

    fireEvent.change(screen.getByLabelText('鼠标输入模式'), { target: { value: 'raw' } })

    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ mouse_mode: 'raw' }))
  })

  it('shows layout conflicts instead of silently accepting them', () => {
    render(
      <ControlSettingsPanel
        suite="mobile-fps"
        value={defaultControlSettings('mobile-fps')}
        bounds={bounds}
        safety={{ usable: bounds, reserved: [{ x: 520, y: 220, width: 120, height: 140 }] }}
        onChange={() => undefined}
      />,
    )

    expect(screen.getByRole('alert')).toHaveTextContent('保留区域')
  })
})
