/** Read only fixed categories; never return the alert text or any account/result data. */
export function readHostInterruption(): Record<string, boolean> {
  const text = document.querySelector('[aria-label="测试场景"] > p[role="alert"]')?.textContent ?? ''
  const labels = {
    interruption_stage_resize: '实际场地尺寸已变化，本项已中断。',
    interruption_countdown_gap: '倒计时帧间隙超过 500ms，本项已中断。',
    interruption_frame_gap: '帧间隙超过 500ms，本项已中断。',
    interruption_clock_invalid: '计时时间异常，本项已中断，请重新开始。',
    interruption_blur: '窗口失去焦点，本项已中断。',
    interruption_hidden: '页面进入后台，本项已中断。',
    interruption_rotation: '屏幕方向变化，本项已中断。',
    interruption_lock_loss: '鼠标锁定已退出，本项已中断。',
    interruption_short_lease: '服务器占用时间不足 63 秒，请对账后重新开始。',
    interruption_start_changed: '启动期间设备状态变化，请对账后重新开始。',
    interruption_touch_cancel: '触点已取消，本项已中断。',
  }
  const host = document.querySelector('[aria-label="测试场景"]')
  const headings = [...document.querySelectorAll('h1,h2,h3')].map(element => element.textContent)
  const phase = host?.getAttribute('data-host-phase')
  return {
    ...Object.fromEntries(Object.entries(labels).map(([key, value]) => [key, text === value])),
    context_host_present: host !== null,
    context_host_intro: phase === 'intro',
    context_host_preflight: phase === 'preflight',
    context_host_trial: phase === 'trial',
    context_host_countdown: phase === 'countdown',
    context_host_running: phase === 'running',
    context_host_interrupted: phase === 'interrupted',
    context_landing_heading: headings.includes('天赋评测 2'),
    context_practice_heading: headings.includes('自由练习'),
    context_login_heading: headings.includes('登录工作台'),
    context_workbench_heading: headings.includes('工作台'),
  }
}
