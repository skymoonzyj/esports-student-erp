import { expect, test, type CDPSession, type Page } from '@playwright/test'
import { pressOwnedDesktopEscape } from './helpers/desktopBrowserFocus'

import {
  canvas,
  captureSafeEvidence,
  choosePractice,
  expectPhase,
  finishTrial,
  host,
  loginAsAssessmentPreviewAdmin,
  openAssessmentHome,
  prepareTrial,
  recordFailureContext,
  startMeasured,
  type Activate,
} from './helpers/esportsAssessment'

const tap: Activate = locator => locator.tap()

test.describe.configure({ mode: 'serial' })
test.use({ trace: 'off', screenshot: 'off', video: 'off' })
test.afterEach(async ({ page }, info) => { await recordFailureContext(page, info) })

async function noHorizontalOverflow(page: Page): Promise<void> {
  const fits = await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)
  expect(fits).toBe(true)
}

async function mobileCanvasCenter(page: Page): Promise<{ x: number; y: number }> {
  const box = await canvas(page, 'mobile-fps').boundingBox()
  if (!box) throw new Error('mobile FPS canvas is not visible')
  return { x: box.x + box.width * .5, y: box.y + box.height * .45 }
}

async function startTouch(cdp: CDPSession, point: { x: number; y: number }): Promise<void> {
  await cdp.send('Input.dispatchTouchEvent', {
    type: 'touchStart',
    touchPoints: [{ id: 71, ...point, radiusX: 2, radiusY: 2, force: 1 }],
  })
}

test('EA-CAP-DESKTOP-001 desktop layout survives resize and pointer-lock loss interrupts practice', async ({ page }, testInfo) => {
  test.setTimeout(45_000)
  expect(testInfo.project.name).toBe('esports-desktop-1440')
  await loginAsAssessmentPreviewAdmin(page)
  await openAssessmentHome(page)
  await noHorizontalOverflow(page)
  await page.setViewportSize({ width: 1024, height: 768 })
  await expect(page.getByRole('heading', { name: '天赋评测 2', exact: true })).toBeVisible()
  await noHorizontalOverflow(page)
  await page.setViewportSize({ width: 1440, height: 900 })

  await choosePractice(page, 'pc-fps', 'precision')
  await prepareTrial(page)
  await expect.poll(() => page.evaluate(() => document.pointerLockElement !== null)).toBe(true)
  await page.mouse.move(730, 460)
  await page.mouse.click(730, 460)
  await finishTrial(page)
  await startMeasured(page, 'practice')
  await expectPhase(page, 'running')
  await pressOwnedDesktopEscape(page, testInfo.project.name)
  await expectPhase(page, 'interrupted')
  await expect(page.getByRole('alert')).toContainText('鼠标锁定已退出')
  await expect(page.getByRole('button', { name: '返回项目列表', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: '结果已保存' })).toHaveCount(0)
  await captureSafeEvidence(page, testInfo, {
    viewport_1440: true,
    viewport_1024: true,
    pointer_lock_entered: true,
    pointer_lock_loss_interrupted: true,
  })
})

test('EA-CAP-NONPHONE-002 desktop width never grants a mobile runtime', async ({ page }, testInfo) => {
  expect(testInfo.project.name).toBe('esports-desktop-1440')
  await loginAsAssessmentPreviewAdmin(page)
  await openAssessmentHome(page)
  await choosePractice(page, 'mobile-fps', 'precision')
  await page.getByRole('button', { name: '检查设备并进入试练', exact: true }).click()
  await expectPhase(page, 'preflight')
  const reasons = page.locator('[data-capability-reason]')
  await expect(reasons).toContainText([
    '请使用与本专项匹配的输入设备',
    '本专项需要在真手机环境中进行',
    '未检测到可用的触屏输入',
  ])
  await expect(page.getByText('实际设备检查信息', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '开始练习', exact: true })).toHaveCount(0)
  await captureSafeEvidence(page, testInfo, {
    wide_viewport: true,
    phone_probe_false: true,
    touch_probe_false: true,
    timer_not_started: true,
  })
})

test('EA-CAP-RESPONSIVE-003 portrait entry and empty history remain reachable and landscape touch passes trial', async ({ page }, testInfo) => {
  expect(testInfo.project.name).toBe('esports-touch-844x390')
  await loginAsAssessmentPreviewAdmin(page)
  await openAssessmentHome(page, tap)
  for (const viewport of [{ width: 390, height: 844 }, { width: 360, height: 800 }]) {
    await page.setViewportSize(viewport)
    await expect(page.getByRole('heading', { name: '天赋评测 2', exact: true })).toBeVisible()
    await noHorizontalOverflow(page)
    await page.goto('/esports-assessments/history?view=own&mode=practice')
    await expect(page.getByRole('heading', { name: '评测记录', exact: true })).toBeVisible()
    await expect(page.getByText('暂无已完成练习', { exact: true })).toBeVisible()
    await noHorizontalOverflow(page)
    await page.getByRole('link', { name: '返回天赋评测 2', exact: true }).click()
  }
  await page.setViewportSize({ width: 844, height: 390 })
  await choosePractice(page, 'mobile-moba', 'dodge', { activate: tap })
  await prepareTrial(page, tap)
  await expectPhase(page, 'trial')
  await finishTrial(page, tap)
  await expect(page.getByText('实际设备检查信息', { exact: true })).toBeVisible()
  await captureSafeEvidence(page, testInfo, {
    portrait_390: true,
    portrait_360: true,
    empty_history_reachable: true,
    landscape_touch_trial: true,
  })
})

test('EA-CAP-ROTATE-004 touch cancellation and rotation interrupt a real mobile practice', async ({ page }, testInfo) => {
  test.setTimeout(45_000)
  expect(testInfo.project.name).toBe('esports-touch-844x390')
  await loginAsAssessmentPreviewAdmin(page)
  await openAssessmentHome(page)
  await choosePractice(page, 'mobile-fps', 'move-fire', { activate: tap })
  await prepareTrial(page, tap)
  await finishTrial(page, tap)
  await startMeasured(page, 'practice', tap)
  await expectPhase(page, 'running')

  const cdp = await page.context().newCDPSession(page)
  try {
    await startTouch(cdp, await mobileCanvasCenter(page))
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchCancel', touchPoints: [] })
  } finally {
    await cdp.detach()
  }
  await page.setViewportSize({ width: 390, height: 844 })
  await expectPhase(page, 'interrupted')
  await expect(page.getByRole('alert')).toContainText(/触点已取消|屏幕方向变化|实际场地尺寸已变化/)
  await expect(page.getByRole('button', { name: '返回项目列表', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: '结果已保存' })).toHaveCount(0)
  await captureSafeEvidence(page, testInfo, {
    real_practice_started: true,
    touch_cancel_dispatched: true,
    landscape_to_portrait: true,
    runtime_interrupted: true,
  })
})

test('EA-CAP-WEBKIT-005 WebKit exposes real capability outcomes without allocating a run', async ({ page }, testInfo) => {
  expect(testInfo.project.name).toBe('esports-webkit-capabilities')
  await loginAsAssessmentPreviewAdmin(page)
  await openAssessmentHome(page)
  await choosePractice(page, 'pc-moba', 'last-hit')
  let allocations = 0
  const countAllocation = (request: { method(): string; url(): string }) => {
    if (request.method() === 'POST' && /\/api\/v1\/esports-assessments\/(?:practice-runs|sessions\/[a-f0-9-]+\/attempts)$/.test(new URL(request.url()).pathname)) allocations += 1
  }
  page.on('request', countAllocation)
  try {
    await page.getByRole('button', { name: '检查设备并进入试练', exact: true }).click()
    await expect.poll(async () => host(page).getAttribute('data-host-phase')).toMatch(/^(trial|preflight)$/)
    const phase = await host(page).getAttribute('data-host-phase')
    if (phase === 'trial') {
      await finishTrial(page)
      await expect(page.getByRole('button', { name: '开始练习', exact: true })).toBeEnabled()
    } else {
      await expect(page.locator('[data-capability-reason]')).not.toHaveCount(0)
      await expect(page.getByRole('button', { name: '开始练习', exact: true })).toHaveCount(0)
    }
    await expect(page.getByText('实际设备检查信息', { exact: true })).toBeVisible()
    expect(allocations).toBe(0)
    await captureSafeEvidence(page, testInfo, {
      webkit_probe_visible: true,
      trial_or_reason_visible: true,
      no_attempt_allocated: true,
    })
  } finally { page.off('request', countAllocation) }
})
