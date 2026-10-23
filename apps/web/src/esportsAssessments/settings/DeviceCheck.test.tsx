import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { EnvironmentSnapshot } from '../contracts'
import { DeviceCheck } from './DeviceCheck'

function observed(overrides: Partial<EnvironmentSnapshot> = {}): EnvironmentSnapshot {
  return {
    input_kind: 'mouse',
    is_phone: false,
    touch_capable: false,
    width_css_px: 800,
    height_css_px: 450,
    device_pixel_ratio: 2,
    pointer_lock_supported: true,
    pointer_lock_active: true,
    raw_mouse_requested: false,
    raw_mouse_confirmed: false,
    webgl2_supported: true,
    ...overrides,
  }
}

describe('DeviceCheck', () => {
  it('requires an explicit click to request Pointer Lock', () => {
    const request = vi.fn()
    render(
      <DeviceCheck
        suite="pc-fps"
        observed={observed({ pointer_lock_active: false })}
        onRequestPointerLock={request}
      />,
    )

    expect(request).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '启用鼠标锁定' }))
    expect(request).toHaveBeenCalledTimes(1)
    expect(screen.getByText('设备尚未通过检查')).toBeInTheDocument()
  })

  it('keeps raw mouse requested and confirmed states visibly distinct', () => {
    const { rerender } = render(
      <DeviceCheck
        suite="pc-fps"
        observed={observed({ raw_mouse_requested: true, raw_mouse_confirmed: false })}
      />,
    )
    expect(screen.getByText('已请求原始鼠标输入，但浏览器未确认')).toBeInTheDocument()

    rerender(
      <DeviceCheck
        suite="pc-fps"
        observed={observed({ raw_mouse_requested: true, raw_mouse_confirmed: true })}
      />,
    )
    expect(screen.getByText('浏览器已确认原始鼠标输入')).toBeInTheDocument()
  })

  it('allows a matching phone without requiring orientation lock support', () => {
    render(
      <DeviceCheck
        suite="mobile-moba"
        observed={observed({
          input_kind: 'touch',
          is_phone: true,
          touch_capable: true,
          width_css_px: 640,
          height_css_px: 360,
          pointer_lock_supported: false,
          pointer_lock_active: false,
        })}
      />,
    )

    expect(screen.getByText('设备检查通过')).toBeInTheDocument()
  })

  it('shows an explicit mismatch for unknown input', () => {
    render(<DeviceCheck suite="pc-moba" observed={observed({ input_kind: 'unknown' })} />)

    expect(screen.getByRole('alert')).toHaveTextContent('输入设备')
    expect(screen.getByText('设备尚未通过检查')).toBeInTheDocument()
  })
})
