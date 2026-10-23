import type { EnvironmentSnapshot, SuiteId } from '../contracts'

export type CapabilityReason =
  | 'input_kind'
  | 'phone_environment'
  | 'touch_capability'
  | 'landscape'
  | 'stage_size'
  | 'pointer_lock_unsupported'
  | 'pointer_lock_inactive'
  | 'webgl2_unsupported'

export interface CapabilityCheck {
  readonly allowed: boolean
  readonly reasons: readonly CapabilityReason[]
}

export const capabilityReasonLabels: Readonly<Record<CapabilityReason, string>> = {
  input_kind: '请使用与本专项匹配的输入设备',
  phone_environment: '本专项需要在真手机环境中进行',
  touch_capability: '未检测到可用的触屏输入',
  landscape: '请将手机手动旋转为横屏',
  stage_size: '当前游戏区域过小，请调整窗口或设备',
  pointer_lock_unsupported: '当前浏览器不支持鼠标锁定',
  pointer_lock_inactive: '请点击按钮进入鼠标锁定后再开始',
  webgl2_unsupported: '当前浏览器不支持本专项所需的 WebGL2',
}

export function checkCapabilities(suite: SuiteId, observed: EnvironmentSnapshot): CapabilityCheck {
  const mobile = suite.startsWith('mobile')
  const fps = suite.endsWith('fps')
  const reasons: CapabilityReason[] = []

  if (observed.input_kind !== (mobile ? 'touch' : 'mouse')) reasons.push('input_kind')

  if (mobile) {
    if (!observed.is_phone) reasons.push('phone_environment')
    if (!observed.touch_capable) reasons.push('touch_capability')
    if (observed.width_css_px <= observed.height_css_px) reasons.push('landscape')
    if (observed.width_css_px < 560 || observed.height_css_px < 280) reasons.push('stage_size')
  } else if (observed.width_css_px < 800 || observed.height_css_px < 450) {
    reasons.push('stage_size')
  }

  if (fps && !observed.webgl2_supported) reasons.push('webgl2_unsupported')
  if (suite === 'pc-fps') {
    if (!observed.pointer_lock_supported) reasons.push('pointer_lock_unsupported')
    if (!observed.pointer_lock_active) reasons.push('pointer_lock_inactive')
  }

  return { allowed: reasons.length === 0, reasons }
}
