/** Self-contained for page.evaluate. Read existing saved probe only; never touch a canvas context. */
export function readPcPreflight(document = globalThis.document) {
  const capabilities = ['pointer_lock_supported', 'pointer_lock_active', 'raw_mouse_requested', 'raw_mouse_confirmed', 'webgl2_supported']
  const codes = ['input_kind', 'phone_environment', 'touch_capability', 'landscape', 'stage_size', 'pointer_lock_unsupported', 'pointer_lock_inactive', 'webgl2_unsupported']
  const hosts = document.querySelectorAll('[aria-label="测试场景"][data-host-phase]')
  const host = hosts.length === 1 ? hosts[0] : null
  const probes = host?.querySelectorAll('section[aria-label="设备检查"] details > pre')
  const present = probes?.length === 1
  let value = null
  if (present) {
    const text = probes[0].textContent ?? ''
    try { if (text.length <= 8192) value = JSON.parse(text) } catch { /* Unavailable, never export raw probe. */ }
  }
  const parsed = value !== null && typeof value === 'object' && !Array.isArray(value)
  const validDimensions = parsed && [value.width_css_px, value.height_css_px].every(number => typeof number === 'number' && Number.isFinite(number) && number >= 0 && number <= 16384)
  const validInput = parsed && ['mouse', 'touch', 'unknown'].includes(value.input_kind)
  const reasons = [...(host?.querySelectorAll('section[aria-label="设备检查"] [data-capability-reason]') ?? [])].map(element => element.getAttribute('data-capability-reason'))
  const buttons = [...(host?.querySelectorAll('button') ?? [])].filter(button => button.textContent.trim() === '检查设备并进入试练')
  return {
    probe_present: present, probe_parse_valid: parsed,
    capability_types_valid: parsed && capabilities.every(key => typeof value[key] === 'boolean'),
    input_kind_valid: validInput, input_kind: validInput ? value.input_kind : 'unknown',
    dimensions_valid: validDimensions,
    width_css_px: validDimensions ? Math.floor(value.width_css_px) : null,
    height_css_px: validDimensions ? Math.floor(value.height_css_px) : null,
    width_ge_800: validDimensions && value.width_css_px >= 800,
    height_ge_450: validDimensions && value.height_css_px >= 450,
    ...Object.fromEntries(capabilities.map(key => [key, parsed && value[key] === true])),
    ...Object.fromEntries(codes.map(code => ['reason_' + code, reasons.includes(code)])),
    reason_unknown_present: reasons.some(reason => !codes.includes(reason)),
    alert_present: Boolean(host?.querySelector(':scope > p[role="alert"]')),
    initial_button_count: buttons.length,
    initial_button_enabled: buttons.length === 1 && !buttons[0].disabled,
  }
}

/** Closed typed projection only. Untrusted annotations never become raw retained evidence. */
export function validatePcPreflight(value) {
  const booleans = ['probe_present', 'probe_parse_valid', 'capability_types_valid', 'input_kind_valid', 'dimensions_valid',
    'width_ge_800', 'height_ge_450', 'pointer_lock_supported', 'pointer_lock_active', 'raw_mouse_requested', 'raw_mouse_confirmed', 'webgl2_supported',
    'reason_input_kind', 'reason_phone_environment', 'reason_touch_capability', 'reason_landscape', 'reason_stage_size', 'reason_pointer_lock_unsupported',
    'reason_pointer_lock_inactive', 'reason_webgl2_unsupported', 'reason_unknown_present', 'alert_present', 'initial_button_enabled']
  const keys = [...booleans, 'input_kind', 'width_css_px', 'height_css_px', 'initial_button_count']
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length !== keys.length
    || keys.some(key => !Object.hasOwn(value, key)) || booleans.some(key => typeof value[key] !== 'boolean')
    || !['mouse', 'touch', 'unknown'].includes(value.input_kind)
    || !Number.isInteger(value.initial_button_count) || value.initial_button_count < 0 || value.initial_button_count > 16
    || [value.width_css_px, value.height_css_px].some(number => value.dimensions_valid
      ? !Number.isInteger(number) || number < 0 || number > 16384 : number !== null)) return null
  return Object.fromEntries(keys.map(key => [key, value[key]]))
}
