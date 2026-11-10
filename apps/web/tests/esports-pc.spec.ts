import { expect, test, type Request } from '@playwright/test'
import type { AssessmentContextView, AssessmentReportPage, AttemptDetailView, HistoryPage } from '../src/esportsAssessments/apiContracts'
import type { AssessmentReportView, SessionView } from '../src/esportsAssessments/contracts'
import { laneWorldToScreen } from '../src/esportsAssessments/lane/projection'
import { parseAssessmentRanking } from '../src/assessmentRankings/contracts'
import { pressOwnedDesktopEscape } from './helpers/desktopBrowserFocus'
import {
  SUITES, canvas, host, captureSafeEvidence, chooseFormal, choosePractice, chooseProject,
  currentCatalogVersion, exercisePcInput, expectCurrentTaskIdentity, expectPhase, finishTrial, loginAsAssessmentPreviewAdmin, loginAsAssessmentParticipant,
  observeNativeActiveRun, observeUiSave, openAssessmentHome, prepareTrial, readAssessmentJson, recordFailureContext,
  startMeasured, expectStableStageLayout, observeStageLayout,
  expectLaneHudBounds, observeLaneHudBounds, parseLaneLifeFeedback,
} from './helpers/esportsAssessment'

test.describe.configure({ mode: 'serial' })
test.setTimeout(480_000)
test.afterEach(async ({ page }, info) => { await recordFailureContext(page, info) })
const pcSuites = ['pc-fps', 'pc-moba'] as const
const durationMs = (suite: typeof pcSuites[number]) => suite === 'pc-moba' ? 300000 : 60000

test('EA-PC-REAL-001 real 60-second FPS and five-minute MOBA input, pointer lock, save and continue', async ({ page }, testInfo) => {
  // No Playwright clock: server-frozen task durations run in real time.
  await loginAsAssessmentPreviewAdmin(page)
  for (const suite of pcSuites) {
    await openAssessmentHome(page)
    await chooseFormal(page, suite)
    const first = SUITES[suite].tests[0]
    await chooseProject(page, first, undefined, suite === 'pc-moba' ? 5 : 3)
    await prepareTrial(page, undefined, 'pc_initial_trial')
    if (suite === 'pc-fps') {
      expect(await page.evaluate(() => document.pointerLockElement !== null)).toBe(true)
      await pressOwnedDesktopEscape(page, testInfo.project.name)
      await expectPhase(page, 'interrupted', 'pc_pointer_lock_exit')
      await expect(page.getByRole('alert')).toContainText('鼠标锁定已退出')
      // Native browsers impose a short re-lock cooldown after Escape.
      await page.waitForTimeout(1_500)
      await prepareTrial(page, undefined, 'pc_reentry_trial')
      expect(await page.evaluate(() => document.pointerLockElement !== null)).toBe(true)
    }
    await finishTrial(page, undefined, 'pc_trial_finish')
    const save = observeUiSave(page, 'formal')
    const nativeRun = await observeNativeActiveRun(page, durationMs(suite))
    try {
      await startMeasured(page, 'formal', undefined, 'pc_formal_countdown')
      await expectPhase(page, 'running', 'pc_formal_running')
      await exercisePcInput(page, suite, first)
      const saved = await save.waitSaved(durationMs(suite) + 30000)
      expect(await nativeRun.activeDurationMs(), 'native finish must reach the signed logical deadline').toBeGreaterThanOrEqual(durationMs(suite))
      expect(save.postCount()).toBe(1)
      expect(save.operationKeysAgree()).toBe(true)
      expect(save.inputResponded(first), 'several actual inputs must affect saved metrics').toBe(true)
      expect(saved.mode === 'formal' && saved.session.results.length === 1).toBe(true)
      if (saved.mode === 'formal') expectCurrentTaskIdentity(saved.session.suite_config.tasks[first], suite, first)
      if (suite === 'pc-moba') {
        expect(saved.mode === 'formal' && saved.session.status === 'completed' && saved.session.report_id !== null).toBe(true)
        await expect(page.getByRole('heading', { name: '端游 MOBA专项报告', exact: true })).toBeVisible()
      } else {
        await expect(page.getByRole('region', { name: '结果已保存', exact: true })).toBeVisible()
        await page.getByRole('button', { name: '继续下一项', exact: true }).click()
        await expect(page.getByText(`已保存 1 / ${SUITES[suite].tests.length}`, { exact: true })).toBeVisible()
      }
      await captureSafeEvidence(page, testInfo, { signed_duration_reached: true, mouse_input_responded: true, keyboard_trial_navigation: true, server_saved: true })
    } finally { save.dispose(); await nativeRun.dispose() }
  }
  const context = await readAssessmentJson<AssessmentContextView>(page, '/context')
  expect(context.active_run).toBeNull()
  expect(context.own_in_progress_sessions).toHaveLength(1)
  expect(context.own_in_progress_sessions[0].suite_id).toBe('pc-fps')
  expect(context.own_in_progress_sessions[0].results).toHaveLength(1)
})

test('EA-PC-REAL-002 real-time continuation of two FPS formal items and two independent practices', async ({ page }, testInfo) => {
  test.setTimeout(pcSuites.reduce((sum, suite) => sum + SUITES[suite].tests.length * (durationMs(suite) + 10000), 0) + 120000)
  await loginAsAssessmentPreviewAdmin(page)
  for (const suite of pcSuites) {
    const remaining = SUITES[suite].tests.slice(1)
    if (remaining.length) { await openAssessmentHome(page); await chooseFormal(page, suite, { resume: true }) }
    for (const [index, item] of remaining.entries()) {
      await chooseProject(page, item)
      await prepareTrial(page)
      await finishTrial(page)
      const save = observeUiSave(page, 'formal')
      const nativeRun = await observeNativeActiveRun(page)
      try {
        await startMeasured(page, 'formal')
        await expectPhase(page, 'running')
        await exercisePcInput(page, suite, item)
        const saved = await save.waitSaved()
        expect(await nativeRun.activeDurationMs(), 'native finish must reach the fixed 60-second logical deadline').toBeGreaterThanOrEqual(60_000)
        expect(save.postCount()).toBe(1)
        expect(save.operationKeysAgree()).toBe(true)
        expect(saved.mode === 'formal' && saved.session.results.length === index + 2).toBe(true)
        if (saved.mode === 'formal') expectCurrentTaskIdentity(saved.session.suite_config.tasks[item], suite, item)
        if (item === 'sixshot') expect(save.inputResponded(item), 'hold contributes one shot and four separate clicks contribute four more').toBe(true)
        if (index < remaining.length - 1) {
          await expect(page.getByRole('region', { name: '结果已保存', exact: true })).toBeVisible()
          await page.getByRole('button', { name: '继续下一项', exact: true }).click()
        } else {
          await expect(page.getByRole('heading', { name: `${SUITES[suite].label}专项报告`, exact: true })).toBeVisible()
          expect(saved.mode === 'formal' && saved.session.status === 'completed' && saved.session.report_id !== null).toBe(true)
        }
      } finally { save.dispose(); await nativeRun.dispose() }
    }
    await openAssessmentHome(page)
    const first = SUITES[suite].tests[0]
    await choosePractice(page, suite, first)
    await prepareTrial(page)
    await finishTrial(page)
    const save = observeUiSave(page, 'practice')
    const nativeRun = await observeNativeActiveRun(page, durationMs(suite))
    try {
      await startMeasured(page, 'practice')
      await expectPhase(page, 'running')
      await exercisePcInput(page, suite, first)
      const saved = await save.waitSaved(durationMs(suite) + 30000)
      expect(await nativeRun.activeDurationMs(), 'native finish must reach the signed logical deadline').toBeGreaterThanOrEqual(durationMs(suite))
      expect(save.postCount()).toBe(1)
      expect(save.operationKeysAgree()).toBe(true)
      expect(saved.mode === 'practice' && saved.scored.score === null && saved.scored.grade === null && saved.session === null).toBe(true)
      expect(save.inputResponded(first)).toBe(true)
      await expect(page.getByRole('region', { name: '结果已保存', exact: true })).toContainText('不计入正式进度')
      await captureSafeEvidence(page, testInfo, { signed_duration_reached: true, separate_practice: true, reports_reached: true, server_saved: true })
    } finally { save.dispose(); await nativeRun.dispose() }
  }
  const formal = await readAssessmentJson<HistoryPage>(page, '/history?mode=formal&limit=100')
  const practice = await readAssessmentJson<HistoryPage>(page, '/history?mode=practice&limit=100')
  const reports = await readAssessmentJson<AssessmentReportPage>(page, '/reports?limit=100')
  expect(formal.visible_total === 2 && formal.items.length === 2 && formal.next_cursor === null).toBe(true)
  expect(pcSuites.every(suite => formal.items.some(item => item.mode === 'formal' && item.session.suite_id === suite && item.session.status === 'completed' && item.session.results.length === SUITES[suite].tests.length))).toBe(true)
  expect(practice.visible_total === 2 && practice.items.length === 2 && practice.next_cursor === null).toBe(true)
  expect(pcSuites.every(suite => practice.items.some(item => item.mode === 'practice' && item.suite_id === suite && item.report_id === null && item.scored.score === null))).toBe(true)
  for (const item of practice.items) {
    if (item.mode === 'practice') expectCurrentTaskIdentity(item.config, item.suite_id, item.test_id)
  }
  expect(reports.visible_total === 2 && reports.items.length === 2 && reports.next_cursor === null).toBe(true)
  expect(pcSuites.every(suite => reports.items.filter(item => item.suite_id === suite).length === 1)).toBe(true)
  for (const summary of reports.items) {
    const report = await readAssessmentJson<AssessmentReportView>(page, `/reports/${summary.id}`)
    expect(report.suite.catalog_version).toBe(currentCatalogVersion(summary.suite_id))
    expect(report.suite.test_ids).toEqual(SUITES[summary.suite_id].tests)
    expect(report.results.map(result => result.test_id).sort()).toEqual([...SUITES[summary.suite_id].tests].sort())
    for (const result of report.results) expectCurrentTaskIdentity(result.config, summary.suite_id, result.test_id)
  }
})

// Register only for an explicitly requested, fresh isolated acceptance dataset.
// Even a skipped extra case would violate the ordinary matrix's strict JSON summary.
if (process.env.ESPORTS_E2E_FIXED_BOARD_ACCEPTANCE === '1') {
test.describe('isolated fixed-board native acceptance (opt-in)', () => {
test('EA-PC-V5-001 native fixed-board window/fullscreen trials and three real 60-second formal saves', async ({ page }, testInfo) => {
  test.setTimeout(420_000)
  // Deliberately no simulated clock, injected lock, engine inspection or API writes.
  // Run against the isolated seeded preview account; caller owns seed/cleanup.
  await loginAsAssessmentPreviewAdmin(page)
  const evidence: Record<string, boolean> = {}
  for (const item of ['gridshot', 'sixshot'] as const) {
    for (const fullscreen of [false, true]) {
      await openAssessmentHome(page)
      await choosePractice(page, 'pc-fps', item, { difficulty: 1 })
      await expect(page.getByText(/目标固定在靶区内，视角到边缘会停止转动/)).toBeVisible()
      if (fullscreen) {
        await page.getByRole('button', { name: '进入全屏', exact: true }).click()
        await expect.poll(() => host(page).evaluate(element => document.fullscreenElement === element)).toBe(true)
        await expect(page.getByRole('button', { name: '检查设备并进入试练', exact: true })).toBeEnabled()
      }
      const before = await observeStageLayout(page)
      expect(Math.abs(before.canvas.width - before.canvas.height * 16 / 9)).toBeLessThanOrEqual(1)
      await prepareTrial(page)
      expect(await canvas(page, 'pc-fps').evaluate(element => document.pointerLockElement === element)).toBe(true)
      const after = await observeStageLayout(page)
      expectStableStageLayout(before, after)
      const current = after.canvas
      const feedback = await page.getByLabel('FPS 实时反馈', { exact: true }).boundingBox()
      const toolbar = await host(page).locator('header').boundingBox()
      if (!feedback || !toolbar) throw new Error('outside-canvas feedback is not visible')
      expect(feedback.y + feedback.height).toBeLessThanOrEqual(current!.y)
      expect(feedback.y).toBeGreaterThanOrEqual(toolbar.y)
      expect(feedback.y + feedback.height).toBeLessThanOrEqual(toolbar.y + toolbar.height)
      // Sweep visible-field corners with ordinary native mouse input; never read target state.
      for (const [x, y] of [[.05, .05], [.95, .05], [.95, .95], [.05, .95], [.5, .5]]) {
        await page.mouse.move(current!.x + current!.width * x, current!.y + current!.height * y, { steps: 12 })
        await page.mouse.down(); await page.waitForTimeout(80); await page.mouse.up()
      }
      await expectPhase(page, 'trial')
      evidence[`${item}_${fullscreen ? 'fullscreen' : 'window'}_native_trial`] = true
      await captureSafeEvidence(page, testInfo, { ...evidence, outside_canvas_hud: true, actual_canvas_16_9: true }, `${item}_${fullscreen ? 'fullscreen' : 'window'}`)
      if (fullscreen) {
        await pressOwnedDesktopEscape(page, testInfo.project.name)
        await expectPhase(page, 'interrupted')
        await expect.poll(() => page.evaluate(() => document.pointerLockElement === null)).toBe(true)
        if (await host(page).evaluate(element => document.fullscreenElement === element)) {
          await page.getByRole('button', { name: '退出全屏', exact: true }).click()
        }
        await expect.poll(() => page.evaluate(() => document.fullscreenElement === null)).toBe(true)
        await page.waitForTimeout(1_500)
      } else await finishTrial(page)
    }
  }
  await openAssessmentHome(page)
  await chooseFormal(page, 'pc-fps')
  let reportId: string | null = null
  for (const [index, item] of SUITES['pc-fps'].tests.entries()) {
    await chooseProject(page, item)
    await prepareTrial(page)
    await finishTrial(page)
    const save = observeUiSave(page, 'formal')
    const nativeRun = await observeNativeActiveRun(page)
    try {
      await startMeasured(page, 'formal')
      await expectPhase(page, 'running')
      expect(await canvas(page, 'pc-fps').evaluate(element => document.pointerLockElement === element)).toBe(true)
      await exercisePcInput(page, 'pc-fps', item)
      const saved = await save.waitSaved()
      expect(await nativeRun.activeDurationMs()).toBeGreaterThanOrEqual(60_000)
      expect(save.postCount()).toBe(1)
      expect(save.operationKeysAgree()).toBe(true)
      if (saved.mode !== 'formal') throw new Error('formal result was not server confirmed')
      expect(saved.session.results).toHaveLength(index + 1)
      expectCurrentTaskIdentity(saved.session.suite_config.tasks[item], 'pc-fps', item)
      if (item !== 'spheretrack90') expect(save.inputResponded(item)).toBe(true)
      evidence[`${item}_real_60s_server_saved`] = true
      reportId = saved.session.report_id
      if (index < SUITES['pc-fps'].tests.length - 1) {
        await page.getByRole('button', { name: '继续下一项', exact: true }).click()
      } else {
        expect(saved.session.status).toBe('completed')
        expect(reportId).not.toBeNull()
        await expect(page.getByRole('heading', { name: '端游 FPS专项报告', exact: true })).toBeVisible()
      }
    } finally { save.dispose(); await nativeRun.dispose() }
  }
  const persisted = await readAssessmentJson<AssessmentReportView>(page, `/reports/${reportId}`)
  expect(persisted.suite.catalog_version).toBe('pc-fps-v5')
  expect(persisted.results.map(result => result.test_id).sort()).toEqual(['gridshot', 'sixshot', 'spheretrack90'])
  for (const result of persisted.results) expectCurrentTaskIdentity(result.config, 'pc-fps', result.test_id)
  await captureSafeEvidence(page, testInfo, { ...evidence, independent_server_report: true, outside_canvas_hud: true, actual_canvas_16_9: true })
})
})
}

// This supplemental acceptance uses a separately seeded account/database; it is
// not registered at all in the ordinary strict two-case PC matrix.
if (process.env.ESPORTS_E2E_LANE_ACCEPTANCE === '1') {
test('EA-PC-LANE-001 native melee and ranged five-minute formal runs, real input, separate saved reports', async ({ page }, testInfo) => {
  test.setTimeout(780000)
  await loginAsAssessmentParticipant(page)
  const reportIds: string[] = []
  for (const attack_mode of ['melee', 'ranged'] as const) {
    await openAssessmentHome(page)
    await chooseFormal(page, 'pc-moba', { attack_mode })
    await expect(page.getByText('已保存 0 / 1', { exact: true })).toBeVisible()
    await expect(page.getByText(new RegExp(`人物已锁定为${attack_mode === 'melee' ? '近战' : '远程'}`))).toBeVisible()
    await chooseProject(page, 'last-hit', undefined, 5)
    if (attack_mode === 'melee') {
      // Separate unmeasured trial at the accepted 800x450 stage minimum.
      // Never resize the measured attempt or disable its resize quality gate.
      const originalViewport = page.viewportSize()
      if (!originalViewport) throw new Error('native viewport unavailable')
      await page.setViewportSize({ width: originalViewport.width, height: 690 })
      await prepareTrial(page)
      const minimum = await canvas(page, 'pc-moba').boundingBox()
      if (!minimum) throw new Error('minimum lane stage unavailable')
      expect(minimum.width).toBeCloseTo(800, 1)
      expect(minimum.height).toBeCloseTo(450, 1)
      expectLaneHudBounds(await observeLaneHudBounds(page))
      await captureSafeEvidence(page, testInfo, { native_minimum_trial: true, actual_stage_800_450: true, every_hud_cell_visible: true }, 'lane_minimum_trial')
      await finishTrial(page)
      expectLaneHudBounds(await observeLaneHudBounds(page))
      await captureSafeEvidence(page, testInfo, { native_minimum_preflight: true, actual_stage_800_450: true, every_hud_cell_visible: true }, 'lane_minimum_preflight')
      await page.getByRole('button', { name: '返回项目列表', exact: true }).click()
      await page.setViewportSize(originalViewport)
      await chooseProject(page, 'last-hit', undefined, 5)
    }
    const before = await canvas(page, 'pc-moba').boundingBox()
    if (!before) throw new Error('lane stage unavailable before preparation')
    await prepareTrial(page)
    const during = await canvas(page, 'pc-moba').boundingBox()
    if (!during) throw new Error('lane stage unavailable during trial')
    expect(during.width).toBe(before.width)
    expect(during.height).toBe(before.height)
    expect(Math.abs(during.width - during.height * 16 / 9)).toBeLessThanOrEqual(1)
    const viewportHeight = await page.evaluate(() => window.innerHeight)
    expect(during.y).toBeGreaterThanOrEqual(0)
    expect(during.y + during.height).toBeLessThanOrEqual(viewportHeight)
    const feedback = await page.getByLabel('兵线实时反馈', { exact: true }).boundingBox()
    if (!feedback) throw new Error('lane feedback unavailable')
    expect(feedback.y).toBeGreaterThanOrEqual(during.y + during.height)
    expect(feedback.y + feedback.height).toBeLessThanOrEqual(viewportHeight)
    expectLaneHudBounds(await observeLaneHudBounds(page))
    // Ordinary left clicks at the upper and side playable edges must remain in
    // the scene without pointer lock or document-level context-menu interception.
    await page.mouse.click(during.x + during.width * .05, during.y + during.height * .1)
    await page.mouse.click(during.x + during.width * .95, during.y + during.height * .5)
    await expectPhase(page, 'trial')
    expect(await page.evaluate(() => document.pointerLockElement === null)).toBe(true)
    await captureSafeEvidence(page, testInfo, { native_trial: true, outside_canvas_hud: true, actual_canvas_16_9: true }, `lane_${attack_mode}_trial`)
    await finishTrial(page)
    expectLaneHudBounds(await observeLaneHudBounds(page))
    const save = observeUiSave(page, 'formal')
    const nativeRun = await observeNativeActiveRun(page, 300000)
    try {
      await startMeasured(page, 'formal')
      const countdownCanvas = await canvas(page, 'pc-moba').boundingBox()
      if (!countdownCanvas) throw new Error('lane canvas disappeared during countdown')
      expect(countdownCanvas.y).toBeGreaterThanOrEqual(0)
      expect(countdownCanvas.y + countdownCanvas.height).toBeLessThanOrEqual(viewportHeight)
      await expectPhase(page, 'running')
      const runningCanvas = await canvas(page, 'pc-moba').boundingBox()
      if (!runningCanvas) throw new Error('lane canvas disappeared after formal start')
      expect(runningCanvas.y).toBeGreaterThanOrEqual(0)
      expect(runningCanvas.y + runningCanvas.height).toBeLessThanOrEqual(viewportHeight)
      expect(runningCanvas.width).toBe(during.width)
      expect(runningCanvas.height).toBe(during.height)
      expectLaneHudBounds(await observeLaneHudBounds(page))
      await captureSafeEvidence(page, testInfo, { native_running: true, outside_canvas_hud: true }, `lane_${attack_mode}_running`)
      await exercisePcInput(page, 'pc-moba', 'last-hit')
      await captureSafeEvidence(page, testInfo, { native_combat: true, left_button_input: true }, `lane_${attack_mode}_combat`)
      if (attack_mode === 'melee') {
        // Frozen public tower coordinates and ordinary canvas input only. No
        // engine state, artificial clock, synthetic damage or raw injection.
        const feedback = page.getByLabel('兵线实时反馈', { exact: true })
        // Initial selected-target combat can already have killed the hero.
        // Wait for a visible live life before issuing the dedicated command;
        // death counts are relative to this observed life, never assumed zero.
        await expect.poll(async () => parseLaneLifeFeedback(await feedback.innerText()).health, { timeout: 7000 }).toBeGreaterThan(0)
        const baselineDeaths = parseLaneLifeFeedback(await feedback.innerText()).deaths
        expectLaneHudBounds(await observeLaneHudBounds(page))
        const enterTower = laneWorldToScreen({ x: 930, y: 400 })
        await page.mouse.click(runningCanvas.x + enterTower.x / 1000 * runningCanvas.width, runningCanvas.y + enterTower.y / 562.5 * runningCanvas.height)
        await expect(feedback).toContainText(/生命 (?:[1-9]\d{0,2}) \/ 1000/, { timeout: 25000 })
        expectLaneHudBounds(await observeLaneHudBounds(page))
        await expect(feedback).toContainText('生命 0 / 1000', { timeout: 25000 })
        expect(parseLaneLifeFeedback(await feedback.innerText()).deaths).toBe(baselineDeaths + 1)
        await expect(feedback).toContainText(/复活倒计时 [1-5] 秒/)
        await expectPhase(page, 'running')
        expect(await canvas(page, 'pc-moba').boundingBox()).toEqual(runningCanvas)
        expectLaneHudBounds(await observeLaneHudBounds(page))
        const deadClock = await feedback.innerText()
        await captureSafeEvidence(page, testInfo, { native_tower_damage: true, native_hero_death: true, clock_continues: true, canvas_unchanged: true }, 'lane_melee_death')
        // Input while dead must not replay after the five-second respawn.
        await page.mouse.click(runningCanvas.x + enterTower.x / 1000 * runningCanvas.width, runningCanvas.y + enterTower.y / 562.5 * runningCanvas.height)
        await expect(feedback).toContainText('生命 1000 / 1000', { timeout: 7000 })
        await expect(feedback).not.toContainText('复活倒计时')
        await page.waitForTimeout(5500)
        await expect(feedback).toContainText('生命 1000 / 1000')
        expect(parseLaneLifeFeedback(await feedback.innerText()).deaths).toBe(baselineDeaths + 1)
        expect(await feedback.innerText()).not.toBe(deadClock)
        await expectPhase(page, 'running')
        expect(await canvas(page, 'pc-moba').boundingBox()).toEqual(runningCanvas)
        expectLaneHudBounds(await observeLaneHudBounds(page))
        // Resume normal input after respawn, away from the enemy towers.
        const safe = laneWorldToScreen({ x: 200, y: 420 })
        await page.mouse.click(runningCanvas.x + safe.x / 1000 * runningCanvas.width, runningCanvas.y + safe.y / 562.5 * runningCanvas.height)
        await captureSafeEvidence(page, testInfo, { native_full_health_respawn: true, no_dead_input_replay: true, post_respawn_input: true, clock_continues: true, canvas_unchanged: true }, 'lane_melee_respawn')
      }
      const saved = await save.waitSaved(330000)
      expect(await nativeRun.activeDurationMs()).toBeGreaterThanOrEqual(300000)
      expect(save.postCount()).toBe(1)
      expect(save.operationKeysAgree()).toBe(true)
      expect(save.inputResponded('last-hit'), 'actual selected-target input must release at least three attacks').toBe(true)
      if (saved.mode !== 'formal' || !saved.session.report_id) throw new Error('lane formal report not persisted')
      expect(saved.session.status).toBe('completed')
      expect(saved.session.members).toEqual(['last-hit'])
      expect(saved.session.results).toHaveLength(1)
      const task = saved.session.suite_config.tasks['last-hit']
      expectCurrentTaskIdentity(task, 'pc-moba', 'last-hit')
      expect(task?.params.kind === 'lane_last_hit_towers' && task.params.attack_mode).toBe(attack_mode)
      await expect(page.getByRole('heading', { name: '端游 MOBA专项报告', exact: true })).toBeVisible()
      await expect(page.getByRole('heading', { name: `五分钟兵线补刀 · ${attack_mode === 'melee' ? '近战' : '远程'}`, exact: true })).toBeVisible()
      const persisted = await readAssessmentJson<AssessmentReportView>(page, `/reports/${saved.session.report_id}`)
      expect(persisted.suite.catalog_version).toBe('pc-moba-v5')
      expect(persisted.results).toHaveLength(1)
      expect(persisted.results[0].scored.metrics.attacks).toBeGreaterThanOrEqual(3)
      expectCurrentTaskIdentity(persisted.results[0].config, 'pc-moba', 'last-hit')
      expect(persisted.results[0].config.params.kind === 'lane_last_hit_towers' && persisted.results[0].config.params.attack_mode).toBe(attack_mode)
      const metrics = persisted.results[0].scored.metrics
      expect(metrics.hero_deaths).toBeGreaterThanOrEqual(attack_mode === 'melee' ? 1 : 0)
      expect(metrics.hero_damage_taken).toBeGreaterThanOrEqual(attack_mode === 'melee' ? 1000 : 0)
      expect(Number(metrics.enemy_deaths) + Number(metrics.unresolved)).toBe(75)
      expect(Number(metrics.opportunity_completion)).toBeCloseTo(Number(metrics.last_hits) / 75, 4)
      await expect(page.getByText(/总机会完成率 = 补刀数 \/ 75/)).toBeVisible()
      const currentMetrics = page.getByRole('region', { name: `五分钟兵线补刀 · ${attack_mode === 'melee' ? '近战' : '远程'}`, exact: true }).locator(':scope > dl.assessmentReportsMetrics')
      await expect(currentMetrics).toHaveCount(1)
      await expect(currentMetrics.getByText('人物死亡次数', { exact: true })).toBeVisible()
      await expect(currentMetrics.getByText('承受防御塔伤害', { exact: true })).toBeVisible()
      reportIds.push(saved.session.report_id)
      await captureSafeEvidence(page, testInfo, { real_active_300s: true, independent_server_report: true, left_button_metrics_responded: true }, `lane_${attack_mode}_report`)
    } finally { save.dispose(); await nativeRun.dispose() }
  }
  expect(new Set(reportIds).size).toBe(2)
  const reports = await readAssessmentJson<AssessmentReportPage>(page, '/reports?limit=100')
  expect(reports.visible_total).toBe(2)
  expect(reports.items).toHaveLength(2)
  expect(reports.next_cursor).toBeNull()
  expect(reports.items.every(report => report.suite_id === 'pc-moba' && reportIds.includes(report.id))).toBe(true)
})

test('EA-PC-LANE-003 read-only continuation rechecks both existing saved v5 reports and current metrics without replaying measured runs', async ({ page }, testInfo) => {
  test.setTimeout(120000)
  await loginAsAssessmentParticipant(page)
  let assessmentWrites = 0
  const onRequest = (request: Request) => {
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method()) && new URL(request.url()).pathname.startsWith('/api/v1/esports-assessments/')) assessmentWrites += 1
  }
  page.on('request', onRequest)
  try {
    const reportsBefore = await readAssessmentJson<AssessmentReportPage>(page, '/reports?limit=100')
    expect(reportsBefore.visible_total).toBe(2)
    expect(reportsBefore.items).toHaveLength(2)
    expect(reportsBefore.next_cursor).toBeNull()
    expect(reportsBefore.items.every(report => report.suite_id === 'pc-moba')).toBe(true)
    const formalBefore = await readAssessmentJson<HistoryPage>(page, '/history?mode=formal&limit=100')
    const practiceBefore = await readAssessmentJson<HistoryPage>(page, '/history?mode=practice&limit=100')
    expect(formalBefore.visible_total).toBe(2)
    expect(formalBefore.items).toHaveLength(2)
    expect(formalBefore.next_cursor).toBeNull()
    expect(practiceBefore).toEqual({ items: [], next_cursor: null, visible_total: 0 })
    const reportIds = reportsBefore.items.map(report => report.id).sort()
    expect(formalBefore.items.map(item => item.mode === 'formal' && item.session.report_id).sort()).toEqual(reportIds)
    for (const item of formalBefore.items) {
      if (item.mode !== 'formal') throw new Error('continuation requires only the existing formal history')
      expect(item.session.status).toBe('completed')
      expect(item.session.suite_id).toBe('pc-moba')
      expect(item.session.suite_config.catalog_version).toBe('pc-moba-v5')
      expect(item.session.members).toEqual(['last-hit'])
      expect(item.session.results).toHaveLength(1)
      expectCurrentTaskIdentity(item.session.suite_config.tasks['last-hit'], 'pc-moba', 'last-hit')
    }
    await openAssessmentHome(page)
    await page.getByRole('link', { name: '查看评测记录', exact: true }).click()
    await page.getByRole('tab', { name: '已完成报告', exact: true }).click()
    const modes: string[] = []
    for (const summary of reportsBefore.items) {
      const persisted = await readAssessmentJson<AssessmentReportView>(page, `/reports/${summary.id}`)
      expect(persisted.id).toBe(summary.id)
      expect(persisted.session_id).toBe(summary.session_id)
      expect(persisted.suite_id).toBe('pc-moba')
      expect(persisted.suite.catalog_version).toBe('pc-moba-v5')
      expect(persisted.suite.test_ids).toEqual(['last-hit'])
      expect(persisted.results).toHaveLength(1)
      const result = persisted.results[0]
      expectCurrentTaskIdentity(result.config, 'pc-moba', 'last-hit')
      expect(persisted.suite.tasks['last-hit']).toEqual(result.config)
      if (result.config.params.kind !== 'lane_last_hit_towers') throw new Error('continuation requires the frozen tower lane configuration')
      const mode = result.config.params.attack_mode
      modes.push(mode)
      const metrics = result.scored.metrics
      expect(result.scored.quality_flags).toEqual([])
      expect(metrics.attacks).toBeGreaterThanOrEqual(3)
      expect(metrics.hero_deaths).toBeGreaterThanOrEqual(mode === 'melee' ? 1 : 0)
      expect(Number.isInteger(metrics.hero_deaths)).toBe(true)
      expect(metrics.hero_damage_taken).toBeGreaterThanOrEqual(Number(metrics.hero_deaths) * 1000)
      expect(metrics.hero_damage_taken).toBeLessThan((Number(metrics.hero_deaths) + 1) * 1000)
      expect(Number(metrics.enemy_deaths) + Number(metrics.unresolved)).toBe(75)
      expect(Number(metrics.last_hits) + Number(metrics.missed)).toBe(Number(metrics.enemy_deaths))
      expect(Number(metrics.opportunity_completion)).toBeCloseTo(Number(metrics.last_hits) / 75, 4)
      expect(result.scored.score).toBe(Math.floor(100 * Number(metrics.last_hits) / 75 + .5))
      const rankingResponse = page.waitForResponse(response => response.request().method() === 'GET'
        && new URL(response.url()).pathname === `/api/v1/esports-assessments/reports/${summary.id}/ranking`)
      const reportLink = page.getByRole('link', { name: '查看报告', exact: true }).and(page.locator(`a[href="/esports-assessments/reports/${summary.id}"]`))
      await reportLink.click()
      await expect(page.getByRole('heading', { name: '端游 MOBA专项报告', exact: true })).toBeVisible()
      const current = page.getByRole('region', { name: `五分钟兵线补刀 · ${mode === 'melee' ? '近战' : '远程'}`, exact: true })
      const currentMetrics = current.locator(':scope > dl.assessmentReportsMetrics')
      await expect(currentMetrics).toHaveCount(1)
      const metricChecks = [
        ['人物死亡次数', `${metrics.hero_deaths} 次`], ['承受防御塔伤害', `${metrics.hero_damage_taken} 点`],
        ['成功补刀数', `${metrics.last_hits} 次`], ['已死亡敌兵', `${metrics.enemy_deaths} 次`],
        ['漏刀数', `${metrics.missed} 次`], ['未结算敌兵', `${metrics.unresolved} 次`], ['普攻释放数', `${metrics.attacks} 次`],
        ['总机会完成率', `${Number((Number(metrics.opportunity_completion) * 100).toFixed(2))}%`],
      ]
      for (const [label, value] of metricChecks) {
        const row = currentMetrics.locator(':scope > div').filter({ has: page.getByText(label, { exact: true }) })
        await expect(row).toHaveCount(1)
        await expect(row.locator(':scope > dt')).toHaveText(label)
        await expect(row.locator(':scope > dd')).toHaveText(value)
      }
      await expect(current.getByText(/总机会完成率 = 补刀数 \/ 75/)).toBeVisible()
      const response = await rankingResponse
      expect(response.ok()).toBe(true)
      const ranking = parseAssessmentRanking(await response.json(), summary.id, ['last-hit'])
      expect(ranking.status).toBe('ranked')
      expect(ranking.best_report_id).toBe(summary.id)
      expect(ranking.participant_count).toBe(1)
      expect(ranking.overall_rank).toBe(1)
      expect(ranking.overall_score).toBe(result.scored.score)
      expect(ranking.items).toEqual([{ test_id: 'last-hit', score: result.scored.score, rank: 1, top_scores: [{ rank: 1, score: result.scored.score, gap: 0 }] }])
      expect(ranking.top_scores).toEqual([{ rank: 1, score: result.scored.score, gap: 0 }])
      const rankRegion = page.getByRole('region', { name: '内部训练排名', exact: true })
      await expect(rankRegion.getByText('整体第 1 名 / 同组 1 人', { exact: true })).toBeVisible()
      await expect(rankRegion.getByRole('table', { name: '最佳整轮各项排名', exact: true }).getByRole('rowheader', { name: `五分钟兵线补刀 · ${mode === 'melee' ? '近战' : '远程'}`, exact: true })).toBeVisible()
      expect(await readAssessmentJson<HistoryPage>(page, '/history?mode=formal&limit=100')).toEqual(formalBefore)
      expect(await readAssessmentJson<HistoryPage>(page, '/history?mode=practice&limit=100')).toEqual(practiceBefore)
      await captureSafeEvidence(page, testInfo, { independent_saved_report_rechecked: true, current_metric_values_rechecked: true, frozen_mode_and_ranking_rechecked: true, history_unchanged: true, no_measured_run_started: true }, `lane_${mode}_report`)
      await page.getByRole('link', { name: '返回已完成报告', exact: true }).click()
    }
    expect(modes.sort()).toEqual(['melee', 'ranged'])
    expect(await readAssessmentJson<AssessmentReportPage>(page, '/reports?limit=100')).toEqual(reportsBefore)
    expect(await readAssessmentJson<HistoryPage>(page, '/history?mode=formal&limit=100')).toEqual(formalBefore)
    expect(await readAssessmentJson<HistoryPage>(page, '/history?mode=practice&limit=100')).toEqual(practiceBefore)
    expect(assessmentWrites).toBe(0)
  } finally { page.off('request', onRequest) }
})

test('EA-PC-LANE-002 native window-blur interruption preserves exactly two existing reports and saves no new result', async ({ page }, testInfo) => {
  test.setTimeout(120000)
  await loginAsAssessmentParticipant(page)
  // Playwright normally emulates every page as focused. Restore native focus
  // BEFORE any measured attempt; this alone must not interrupt a foreground page.
  const foreground = await page.context().newPage()
  await page.bringToFront()
  const nativeFocus = await page.context().newCDPSession(page)
  await nativeFocus.send('Emulation.setFocusEmulationEnabled', { enabled: false })
  await expect.poll(() => page.evaluate(() => document.hasFocus())).toBe(true)
  const before = await readAssessmentJson<AssessmentReportPage>(page, '/reports?limit=100')
  expect(before.visible_total).toBe(2)
  expect(before.items).toHaveLength(2)
  expect(before.next_cursor).toBeNull()
  const previousReportIds = before.items.map(report => report.id).sort()
  await openAssessmentHome(page)
  const existing = (await readAssessmentJson<AssessmentContextView>(page, '/context')).own_in_progress_sessions.find(session => session.suite_id === 'pc-moba')
  if (existing) {
    // A prior failed driver run may leave an empty round/attempt. Reconcile and
    // end it through UI only, preserving reports and the original server identity.
    expect(existing.results).toEqual([])
    expect(existing.report_id).toBeNull()
    expect(existing.suite_config.catalog_version).toBe('pc-moba-v5')
    expect(existing.members).toEqual(['last-hit'])
  }
  await chooseFormal(page, 'pc-moba', existing ? { resume: true } : { attack_mode: 'melee' })
  await chooseProject(page, 'last-hit', undefined, 5)
  await expect(host(page).getByText('正在查询服务器恢复状态…', { exact: true })).toHaveCount(0)
  const endOld = page.getByRole('button', { name: '结束未完成尝试并重新开始', exact: true })
  await expect(page.getByRole('button', { name: '检查设备并进入试练', exact: true }).or(endOld)).toBeVisible()
  if (await endOld.isVisible()) {
    await endOld.click()
    await expect.poll(async () => (await readAssessmentJson<AssessmentContextView>(page, '/context')).active_run).toBeNull()
  }
  await prepareTrial(page)
  await finishTrial(page)
  const save = observeUiSave(page, 'formal')
  try {
    await startMeasured(page, 'formal')
    await expectPhase(page, 'running')
    const active = (await readAssessmentJson<AssessmentContextView>(page, '/context')).active_run
    if (!active || active.mode !== 'formal' || !active.session_id || active.suite_id !== 'pc-moba') throw new Error('new formal lane allocation was not observed')
    expect(await page.evaluate(() => document.hasFocus())).toBe(true)
    // Real foreground switch, not synthetic blur/visibility or a hidden override.
    try {
      await foreground.bringToFront()
      await expect.poll(() => page.evaluate(() => document.hasFocus())).toBe(false)
      await expectPhase(page, 'interrupted')
      await expect(host(page).getByRole('alert')).toHaveText('窗口失去焦点，本项已中断。')
      await expect.poll(async () => (await readAssessmentJson<AttemptDetailView>(page, `/attempts/${active.attempt_id}`)).attempt.state).toBe('interrupted')
    } finally { await foreground.close(); await page.bringToFront() }
    expect(save.postCount()).toBe(0)
    const detail = await readAssessmentJson<AttemptDetailView>(page, `/attempts/${active.attempt_id}`)
    expect(detail.attempt.state).toBe('interrupted')
    expect(detail.saved).toBeNull()
    const session = await readAssessmentJson<SessionView>(page, `/sessions/${active.session_id}`)
    expect(session.results).toEqual([])
    expect(session.report_id).toBeNull()
    expect(session.status).toBe('in_progress')
    const after = await readAssessmentJson<AssessmentReportPage>(page, '/reports?limit=100')
    expect(after.visible_total).toBe(2)
    expect(after.items).toHaveLength(2)
    expect(after.next_cursor).toBeNull()
    expect(after.items.map(report => report.id).sort()).toEqual(previousReportIds)
    expect((await readAssessmentJson<AssessmentContextView>(page, '/context')).active_run).toBeNull()
    await captureSafeEvidence(page, testInfo, { real_window_blur: true, exact_blur_reason: true, native_focus_override_disabled: true, server_interrupted: true, no_result_post: true, no_new_result: true, only_two_existing_reports: true }, 'lane_blur_interrupted')
  } finally { save.dispose(); await nativeFocus.detach(); if (!foreground.isClosed()) await foreground.close() }
})
}
