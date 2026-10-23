import { expect, test, type CDPSession, type Page } from '@playwright/test'

import {
  SUITES,
  canvas,
  captureSafeEvidence,
  chooseFormal,
  choosePractice,
  chooseProject,
  expectPhase,
  finishTrial,
  host,
  loginAsAssessmentPreviewAdmin,
  expectFrozenV3TaskIdentity,
  observeNativeActiveRun,
  observeUiSave,
  openAssessmentHome,
  prepareTrial,
  recordFailureContext,
  readAssessmentJson,
  startMeasured,
  type Activate,
  type Suite,
  type TestId,
} from './helpers/esportsAssessment'

type TouchPoint = Readonly<{ id: number; x: number; y: number }>
type FormalHistory = {
  items: Array<{ session: { suite_id: string; status: string; results: Array<{ test_id: string }> } }>
}
type PracticeHistory = { items: Array<{ suite_id: string; test_id: string; state: string }> }

const tap: Activate = locator => locator.tap()

test.describe.configure({ mode: 'serial' })
test.use({ trace: 'off', screenshot: 'off', video: 'off' })
test.afterEach(async ({ page }, info) => { await recordFailureContext(page, info) })

function protocolPoint(point: TouchPoint) {
  return { ...point, radiusX: 2, radiusY: 2, force: 1 }
}

async function dispatchTouch(
  cdp: CDPSession,
  type: 'touchStart' | 'touchMove' | 'touchEnd' | 'touchCancel',
  points: readonly TouchPoint[],
): Promise<void> {
  await cdp.send('Input.dispatchTouchEvent', {
    type,
    touchPoints: points.map(protocolPoint),
  })
}

async function visibleTouchPoints(page: Page, suite: Suite): Promise<{
  move: TouchPoint
  aim: TouchPoint
  action: TouchPoint
  skill?: TouchPoint
}> {
  const box = await canvas(page, suite).boundingBox()
  if (!box) throw new Error('assessment canvas is not visible')
  const visibleControl = async (label: '移动' | '攻击' | '技能', id: number) => {
    const control = mobaControl(page, label)
    if (!await control.count()) return undefined
    const rect = await control.boundingBox()
    if (!rect) throw new Error('visible control has no geometry')
    return { id, x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 }
  }
  return {
    move: (suite === 'mobile-moba' ? await visibleControl('移动', 11) : undefined)
      ?? { id: 11, x: box.x + box.width * .15, y: box.y + box.height * .75 },
    aim: { id: 22, x: box.x + box.width * .58, y: box.y + box.height * .42 },
    action: (suite === 'mobile-moba' ? await visibleControl('攻击', 33) : undefined)
      ?? { id: 33, x: box.x + box.width * .85, y: box.y + box.height * .75 },
    skill: suite === 'mobile-moba' ? await visibleControl('技能', 44) : undefined,
  }
}

async function inputGap(page: Page, milliseconds = 260): Promise<void> {
  await page.waitForTimeout(milliseconds)
}

async function noHorizontalOverflow(page: Page): Promise<void> {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)
}

function mobaControl(page: Page, label: '移动' | '攻击' | '技能') {
  return host(page).locator('.assessmentStageContainer')
    .locator('div[aria-hidden="true"] > div').filter({ hasText: new RegExp(`^${label}$`) })
}

async function dragRangeWithTouch(page: Page, label: string, from: number, to: number): Promise<void> {
  const range = page.getByRole('slider', { name: label, exact: true })
  await expect(range).toBeEnabled()
  // Raw CDP touch coordinates do not auto-scroll like locator.tap().
  await range.scrollIntoViewIfNeeded()
  const box = await range.boundingBox()
  if (!box) throw new Error('control position slider is not visible')
  const cdp = await page.context().newCDPSession(page)
  const point = (id: number, fraction: number): TouchPoint => ({
    id,
    x: box.x + box.width * fraction,
    y: box.y + box.height / 2,
  })
  try {
    await dispatchTouch(cdp, 'touchStart', [point(91, from)])
    await dispatchTouch(cdp, 'touchMove', [point(91, (from + to) / 2)])
    await dispatchTouch(cdp, 'touchMove', [point(91, to)])
    await dispatchTouch(cdp, 'touchEnd', [])
  } finally {
    await cdp.detach()
  }
}

async function reorderVisibleControlsWithoutClickThrough(page: Page): Promise<void> {
  let allocationRequests = 0
  const countAllocation = (request: { method(): string; url(): string }) => {
    if (request.method() === 'POST' && /\/sessions\/[a-f0-9-]+\/attempts$/.test(new URL(request.url()).pathname)) allocationRequests += 1
  }
  page.on('request', countAllocation)
  try {
    await dragRangeWithTouch(page, '移动摇杆横向位置', .15, .7)
    await dragRangeWithTouch(page, '技能按钮横向位置', .72, .2)
    // Layout belongs to the entire suite: even an inactive attack button must
    // not overlap the joystick moved to the right in this skill-only item.
    await dragRangeWithTouch(page, '攻击按钮横向位置', .85, .4)
    await expect.poll(async () => Number(await page.getByRole('slider', { name: '移动摇杆横向位置' }).inputValue())).toBeGreaterThan(.6)
    await expect.poll(async () => Number(await page.getByRole('slider', { name: '技能按钮横向位置' }).inputValue())).toBeLessThan(.3)
    await expect.poll(async () => Number(await page.getByRole('slider', { name: '攻击按钮横向位置' }).inputValue())).toBeGreaterThan(.3)
    await expect.poll(async () => Number(await page.getByRole('slider', { name: '攻击按钮横向位置' }).inputValue())).toBeLessThan(.5)
    await expect(page.getByRole('alert').filter({ hasText: /重叠|超出安全区|覆盖保留区域/ })).toHaveCount(0)

    await canvas(page, 'mobile-moba').scrollIntoViewIfNeeded()
    const move = await mobaControl(page, '移动').boundingBox()
    const skill = await mobaControl(page, '技能').boundingBox()
    if (!move || !skill) throw new Error('reordered controls are not visible')
    expect(move.x).toBeGreaterThan(skill.x)

    const beforeUrl = page.url()
    const cdp = await page.context().newCDPSession(page)
    try {
      const start = { id: 92, x: move.x + move.width / 2, y: move.y + move.height / 2 }
      expect(await canvas(page, 'mobile-moba').evaluate((element, point) => document.elementFromPoint(point.x, point.y) === element, start)).toBe(true)
      await dispatchTouch(cdp, 'touchStart', [start])
      await dispatchTouch(cdp, 'touchMove', [{ ...start, x: start.x + 20 }])
      await dispatchTouch(cdp, 'touchEnd', [])
    } finally {
      await cdp.detach()
    }
    await expectPhase(page, 'intro')
    await expect(page).toHaveURL(beforeUrl)
    await expect(page.getByRole('button', { name: '检查设备并进入试练', exact: true })).toBeVisible()
    expect(allocationRequests, 'a control gesture must not click through into formal allocation').toBe(0)
  } finally {
    page.off('request', countAllocation)
  }
}

async function assertImmersiveSafeArea(page: Page): Promise<void> {
  const layout = await host(page).evaluate(element => {
    const style = getComputedStyle(element)
    const rect = element.getBoundingClientRect()
    return {
      className: element.className,
      padding: [style.paddingTop, style.paddingRight, style.paddingBottom, style.paddingLeft].map(Number.parseFloat),
      rect: { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom },
      viewport: { width: window.innerWidth, height: window.innerHeight },
    }
  })
  expect(layout.className).toContain('assessmentHostImmersive')
  expect(layout.padding.every(value => value >= 8)).toBe(true)
  expect(layout.rect.left >= 0 && layout.rect.top >= 0
    && layout.rect.right <= layout.viewport.width && layout.rect.bottom <= layout.viewport.height).toBe(true)

  const stage = await canvas(page, 'mobile-moba').boundingBox()
  const move = await mobaControl(page, '移动').boundingBox()
  const skill = await mobaControl(page, '技能').boundingBox()
  if (!stage || !move || !skill) throw new Error('safe-area controls are not visible')
  for (const control of [move, skill]) {
    expect(control.x >= stage.x && control.y >= stage.y
      && control.x + control.width <= stage.x + stage.width
      && control.y + control.height <= stage.y + stage.height).toBe(true)
  }
}

async function assertCanvasExposed(page: Page, suite: Suite): Promise<void> {
  const exposed = await canvas(page, suite).evaluate(element => {
    const b = element.getBoundingClientRect()
    return b.left >= 0 && b.top >= 0 && b.right <= innerWidth && b.bottom <= innerHeight
      && [[.5, .5], [.05, .05], [.95, .05], [.05, .95], [.95, .95]]
        .every(([x, y]) => document.elementFromPoint(b.left + b.width * x, b.top + b.height * y) === element)
  })
  expect(exposed, 'the entire active game must be visible and receive input above ERP navigation').toBe(true)
}

async function assertMovementFeedbackVisible(page: Page): Promise<void> {
  const feedback = page.getByRole('region', { name: '移动射击反馈', exact: true })
  await expect(feedback.getByText('移动中命中才有效', { exact: true })).toBeVisible()
  for (const selector of ['.assessmentMobaFeedbackState', '.assessmentMobaFeedbackOutcome']) {
    const text = feedback.locator(selector)
    await expect(text).toBeVisible()
    const visible = await text.evaluate(element => {
      const box = element.getBoundingClientRect()
      const field = document.querySelector('canvas[aria-label="FPS 测试场景"]')!.getBoundingClientRect()
      return Boolean(element.textContent?.trim()) && box.left >= 0 && box.top >= 0
        && box.right <= innerWidth && box.bottom <= innerHeight && box.bottom <= field.top
        && element.scrollWidth <= element.clientWidth && element.scrollHeight <= element.clientHeight
    })
    expect(visible, `${selector} must be fully readable above the canvas without scrolling`).toBe(true)
  }
}

async function exerciseTouchInput(
  page: Page,
  suite: Suite,
  testId: TestId,
): Promise<void> {
  const cdp = await page.context().newCDPSession(page)
  const points = await visibleTouchPoints(page, suite)
  const movingFire = suite === 'mobile-fps' && testId === 'move-fire'
  const initialCanvas = movingFire ? await canvas(page, suite).boundingBox() : null
  if (movingFire) await assertMovementFeedbackVisible(page)
  try {
    for (let index = 0; index < 4; index += 1) {
      const move = { ...points.move, x: points.move.x + 30, y: points.move.y - 8 }
      const aim = { ...points.aim, x: points.aim.x + 18 + index * 4, y: points.aim.y - 8 }
      if (suite === 'mobile-fps' && testId === 'move-fire') {
        await dispatchTouch(cdp, 'touchStart', [points.move, points.aim, points.action])
        await dispatchTouch(cdp, 'touchMove', [move, aim, points.action])
        // Omitting only the fire point keeps movement/aim active while Chromium
        // emits the changed-pointer release. The canvas remains the sole router.
        await dispatchTouch(cdp, 'touchMove', [move, aim])
      } else if (suite === 'mobile-fps') {
        await dispatchTouch(cdp, 'touchStart', [points.aim, points.action])
        await dispatchTouch(cdp, 'touchMove', [aim, points.action])
      } else if (testId === 'skill-aim') {
        const skill = points.skill
        if (!skill) throw new Error('skill control is not visible')
        await dispatchTouch(cdp, 'touchStart', [points.move, skill])
        await dispatchTouch(cdp, 'touchMove', [move, { ...skill, x: skill.x + 70, y: skill.y - 45 }])
      } else {
        await dispatchTouch(cdp, 'touchStart', [points.move, points.action])
        await dispatchTouch(cdp, 'touchMove', [move, points.action])
      }
      await inputGap(page)
      if (movingFire) {
        await assertMovementFeedbackVisible(page)
        const currentCanvas = await canvas(page, suite).boundingBox()
        expect(currentCanvas, 'feedback changes while moving must not shift or resize the canvas').toEqual(initialCanvas)
      }
      await dispatchTouch(cdp, 'touchEnd', [])
      // Rhythm accepts one attack per round with a one-second cooldown. Space
      // commands into separate two-second opportunities instead of flooding
      // four commands into one round and mistaking valid throttling for lost input.
      await inputGap(page, testId === 'move-attack' ? 2_000 : 260)
    }
  } finally {
    await cdp.detach()
  }
}

async function completeTouchItem(
  page: Page,
  suite: Suite,
  testId: TestId,
  mode: 'formal' | 'practice',
  options: {
    requireResponded?: boolean
    finalFormal?: boolean
    verifySafeArea?: boolean
    verifySaveLayout?: boolean
  },
): Promise<void> {
  const save = observeUiSave(page, mode)
  const movingFire = suite === 'mobile-fps' && testId === 'move-fire'
  try {
    // Exercise the narrow supported landscape in trial, then the standard
    // viewport during the real 60-second run. Never resize an active runtime.
    if (movingFire) await page.setViewportSize({ width: 576, height: 387 })
    await prepareTrial(page, tap)
    await assertCanvasExposed(page, suite)
    if (movingFire) {
      const box = await canvas(page, suite).boundingBox()
      expect(box?.width).toBeGreaterThanOrEqual(560)
      expect(box?.height).toBeGreaterThanOrEqual(280)
    }
    if (options.verifySafeArea) await assertImmersiveSafeArea(page)
    await exerciseTouchInput(page, suite, testId)
    await finishTrial(page, tap)
    if (movingFire) await page.setViewportSize({ width: 844, height: 390 })
    const nativeRun = await observeNativeActiveRun(page)
    try {
      await startMeasured(page, mode, tap)
      await expectPhase(page, 'running')
      await assertCanvasExposed(page, suite)
      await exerciseTouchInput(page, suite, testId)
      const beforeSave = options.verifySaveLayout ? await canvas(page, suite).boundingBox() : null
      const saved = await save.waitSaved()
      expect(await nativeRun.activeDurationMs(), 'native finish must reach the fixed 60-second logical deadline').toBeGreaterThanOrEqual(60_000)
      if (options.finalFormal) {
        await expect(page.getByRole('heading', { name: `${SUITES[suite].label}专项报告`, exact: true })).toBeVisible()
      } else {
        await expect(page.getByRole('region', { name: '结果已保存', exact: true })).toBeVisible()
      }
      expect(saved.mode).toBe(mode)
      if (saved.mode === 'formal') expectFrozenV3TaskIdentity(saved.session.suite_config.tasks[testId], suite, testId)
      expect(save.postCount()).toBe(1)
      expect(save.operationKeysAgree()).toBe(true)
      if (options.requireResponded) expect(save.inputResponded(testId)).toBe(true)
      if (beforeSave) {
        const afterSave = await canvas(page, suite).boundingBox()
        if (!afterSave) throw new Error('saved result displaced the assessment canvas')
        for (const key of ['x', 'y', 'width', 'height'] as const) {
          expect(Math.abs(afterSave[key] - beforeSave[key]), `save must not shift canvas ${key}`).toBeLessThanOrEqual(1)
        }
        await noHorizontalOverflow(page)
      }
    } finally { await nativeRun.dispose() }
  } finally {
    save.dispose()
  }
}

async function returnFromSaved(page: Page, mode: 'formal' | 'practice'): Promise<void> {
  await tap(page.getByRole('button', {
    name: mode === 'formal' ? '继续下一项' : '重新练习',
    exact: true,
  }))
}

test('EA-TOUCH-REAL-001 real 60-second mobile items use concurrent touch input', async ({ page }, testInfo) => {
  test.setTimeout(190_000)
  expect(testInfo.project.name).toBe('esports-touch-844x390')
  await loginAsAssessmentPreviewAdmin(page)

  await openAssessmentHome(page)
  await chooseFormal(page, 'mobile-fps', { activate: tap })
  await chooseProject(page, 'move-fire', tap)
  await completeTouchItem(page, 'mobile-fps', 'move-fire', 'formal', { requireResponded: true })
  await returnFromSaved(page, 'formal')

  await openAssessmentHome(page)
  await chooseFormal(page, 'mobile-moba', { activate: tap })
  await chooseProject(page, 'move-attack', tap)
  await completeTouchItem(page, 'mobile-moba', 'move-attack', 'formal', { requireResponded: true })

  const formal = await readAssessmentJson<FormalHistory>(page, '/history?mode=formal&limit=20')
  expect(formal.items).toHaveLength(2)
  expect(formal.items.every(item => item.session.status === 'in_progress')).toBe(true)
  expect(formal.items.map(item => item.session.results.length).sort()).toEqual([1, 1])
  await captureSafeEvidence(page, testInfo, {
    real_duration: true,
    distinct_touch_ids: true,
    concurrent_move_aim_fire: true,
    server_confirmed_save: true,
  })
})

test('EA-TOUCH-REAL-002 real-time continuation completes mobile suites and practices', async ({ page }, testInfo) => {
  const persistedItemCount = 8
  test.setTimeout(persistedItemCount * 70_000 + 120_000)
  expect(testInfo.project.name).toBe('esports-touch-844x390')
  await loginAsAssessmentPreviewAdmin(page)

  for (const suite of ['mobile-fps', 'mobile-moba'] as const) {
    await openAssessmentHome(page)
    await chooseFormal(page, suite, { resume: true, activate: tap })
    const completed = suite === 'mobile-fps' ? 'move-fire' : 'move-attack'
    const remaining = SUITES[suite].tests.filter(value => value !== completed)
    for (const [index, testId] of remaining.entries()) {
      await chooseProject(page, testId, tap)
      const finalFormal = index === remaining.length - 1
      const verifyControls = suite === 'mobile-moba' && testId === 'skill-aim'
      if (verifyControls) await reorderVisibleControlsWithoutClickThrough(page)
      await completeTouchItem(page, suite, testId, 'formal', {
        finalFormal,
        verifySafeArea: verifyControls,
      })
      if (!finalFormal) await returnFromSaved(page, 'formal')
    }
    await expect(page).toHaveURL(/\/esports-assessments\/reports\/[a-f0-9-]+$/)
    await expect(page.getByRole('heading', { name: `${SUITES[suite].label}专项报告`, exact: true })).toBeVisible()
  }

  for (const [suite, testId] of [
    ['mobile-fps', 'precision'],
    ['mobile-moba', 'dodge'],
  ] as const) {
    await openAssessmentHome(page)
    await choosePractice(page, suite, testId, { activate: tap })
    const finalPractice = suite === 'mobile-moba'
    await completeTouchItem(page, suite, testId, 'practice', {
      verifySaveLayout: finalPractice,
    })
    if (!finalPractice) await returnFromSaved(page, 'practice')
  }

  for (const viewport of [{ width: 390, height: 844 }, { width: 360, height: 800 }]) {
    await page.setViewportSize(viewport)
    await expect(page.getByRole('region', { name: '结果已保存', exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '重新练习', exact: true })).toBeVisible()
    await noHorizontalOverflow(page)
  }
  await tap(page.getByRole('button', { name: '重新练习', exact: true }))

  await openAssessmentHome(page)
  await tap(page.getByRole('link', { name: '查看评测记录', exact: true }))
  await tap(page.getByRole('tab', { name: '已完成报告', exact: true }))
  for (const viewport of [{ width: 390, height: 844 }, { width: 360, height: 800 }]) {
    await page.setViewportSize(viewport)
    const reportLink = page.getByRole('link', { name: '查看报告', exact: true }).first()
    await expect(reportLink).toBeVisible()
    await noHorizontalOverflow(page)
    await tap(reportLink)
    await expect(page.getByRole('heading', { name: /手游 (FPS|MOBA)专项报告/ })).toBeVisible()
    await expect(page.locator('.assessmentReportsCard').first()).toBeVisible()
    await noHorizontalOverflow(page)
    await tap(page.getByRole('link', { name: '返回已完成报告', exact: true }))
    await expect(page.getByRole('heading', { name: '评测记录', exact: true })).toBeVisible()
  }

  const formal = await readAssessmentJson<FormalHistory>(page, '/history?mode=formal&limit=20')
  expect(formal.items).toHaveLength(2)
  expect(formal.items.every(item => item.session.status === 'completed' && item.session.results.length === 4)).toBe(true)
  const practice = await readAssessmentJson<PracticeHistory>(page, '/history?mode=practice&limit=20')
  expect(practice.items).toHaveLength(2)
  expect(practice.items.map(item => `${item.suite_id}/${item.test_id}`).sort()).toEqual([
    'mobile-fps/precision',
    'mobile-moba/dodge',
  ])
  await captureSafeEvidence(page, testInfo, {
    real_active_60s: true,
    real_pointer_events: true,
    formal_results_eight: true,
    reports_two: true,
    practices_two: true,
    controls_reordered: true,
    safe_area_respected: true,
    no_gesture_clickthrough: true,
    portrait_saved_result: true,
    portrait_report_reached: true,
    save_layout_stable: true,
  })
})
