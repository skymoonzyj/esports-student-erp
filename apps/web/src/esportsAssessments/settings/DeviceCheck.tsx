import type { EnvironmentSnapshot, SuiteId } from '../contracts'
import {
  capabilityReasonLabels,
  checkCapabilities,
} from '../engineRuntime/capabilities'

export interface DeviceCheckProps {
  readonly suite: SuiteId
  readonly observed: EnvironmentSnapshot
  readonly onRequestPointerLock?: () => void
}

export function DeviceCheck({ suite, observed, onRequestPointerLock }: DeviceCheckProps) {
  const check = checkCapabilities(suite, observed)
  const mobile = suite.startsWith('mobile')

  return (
    <section aria-labelledby="device-check-title">
      <h2 id="device-check-title">设备检查</h2>
      <p>{check.allowed ? '设备检查通过' : '设备尚未通过检查'}</p>

      {check.reasons.length > 0 && (
        <div role="alert">
          <ul>
            {check.reasons.map(reason => (
              <li key={reason}>{capabilityReasonLabels[reason]}</li>
            ))}
          </ul>
        </div>
      )}

      {mobile && <p>手机专项请手动保持横屏；浏览器方向锁定仅作为可选增强。</p>}

      {suite === 'pc-fps' && (
        <>
          <p>{rawMouseStatus(observed)}</p>
          {observed.pointer_lock_supported
            && !observed.pointer_lock_active
            && onRequestPointerLock && (
              <button type="button" onClick={onRequestPointerLock}>启用鼠标锁定</button>
            )}
        </>
      )}

      <dl>
        <dt>游戏区域</dt>
        <dd>{observed.width_css_px} × {observed.height_css_px} CSS px</dd>
        <dt>输入类别</dt>
        <dd>{observed.input_kind}</dd>
      </dl>
      <p>环境信息仅用于本次兼容性与质量判断，不采集设备唯一标识。</p>
    </section>
  )
}

function rawMouseStatus(observed: EnvironmentSnapshot): string {
  if (observed.raw_mouse_confirmed) return '浏览器已确认原始鼠标输入'
  if (observed.raw_mouse_requested) return '已请求原始鼠标输入，但浏览器未确认'
  return '使用普通相对鼠标输入，尚未请求原始模式'
}
