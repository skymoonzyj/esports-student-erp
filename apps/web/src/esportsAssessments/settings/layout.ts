import {
  controlRectSchema,
  controlSettingsSchema,
  type ControlRect,
  type ControlSettings,
  type SuiteId,
} from '../contracts'
import { containsRect, overlaps } from '../engineRuntime/geometry'
import type { Rect } from '../engineRuntime/types'

export interface LayoutSafety {
  readonly usable: Rect
  readonly reserved: readonly Rect[]
}

export function controlToRect(control: ControlRect, bounds: Rect): Rect {
  return {
    x: bounds.x + control.x * bounds.width - control.width / 2,
    y: bounds.y + control.y * bounds.height - control.height / 2,
    width: control.width,
    height: control.height,
  }
}

export function validateLayout(
  layout: readonly ControlRect[],
  bounds: Rect,
  safety: LayoutSafety,
): string[] {
  const errors: string[] = []
  if (bounds.width <= 0 || bounds.height <= 0 || !containsRect(bounds, safety.usable)) {
    errors.push('游戏区域或安全区无效')
  }

  const ids = new Set<string>()
  const validRects: { id: string; rect: Rect }[] = []
  for (const control of layout) {
    if (ids.has(control.id)) errors.push(`控件 ${control.id} 重复`)
    ids.add(control.id)

    if (!controlRectSchema.safeParse(control).success) {
      errors.push(`控件 ${control.id} 的类型或尺寸无效`)
      continue
    }
    const rect = controlToRect(control, bounds)
    if (!containsRect(bounds, rect) || !containsRect(safety.usable, rect)) {
      errors.push(`控件 ${control.id} 超出安全区`)
    }
    if (safety.reserved.some(reserved => overlaps(rect, reserved))) {
      errors.push(`控件 ${control.id} 覆盖保留区域`)
    }
    for (const placed of validRects) {
      if (overlaps(rect, placed.rect)) errors.push(`控件 ${control.id} 与 ${placed.id} 重叠`)
    }
    validRects.push({ id: control.id, rect })
  }

  return errors
}

export function normalizeSensitivity(value: number): number {
  if (!Number.isFinite(value)) return 1
  const clamped = Math.min(2, Math.max(0.5, value))
  return Math.round(clamped * 20) / 20
}

export function defaultControlSettings(suite: SuiteId): ControlSettings {
  if (suite === 'pc-fps') {
    return controlSettingsSchema.parse({ sensitivity: 1, input_kind: 'mouse', mouse_mode: 'relative', controls: [] })
  }
  if (suite === 'pc-moba') {
    return controlSettingsSchema.parse({ sensitivity: 1, input_kind: 'mouse', mouse_mode: null, controls: [] })
  }

  const controls: ControlRect[] = [
    { id: 'move', kind: 'joystick', x: 0.15, y: 0.75, width: 120, height: 120 },
    ...(suite === 'mobile-fps'
      ? [{ id: 'fire', kind: 'button', x: 0.85, y: 0.75, width: 64, height: 64 } as const]
      : [
          { id: 'attack', kind: 'button', x: 0.85, y: 0.75, width: 64, height: 64 } as const,
          { id: 'skill', kind: 'button', x: 0.72, y: 0.75, width: 64, height: 64 } as const,
        ]),
  ]
  return controlSettingsSchema.parse({
    sensitivity: 1,
    input_kind: 'touch',
    mouse_mode: null,
    controls,
  })
}
