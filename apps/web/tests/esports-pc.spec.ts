import { expect, test } from '@playwright/test'
import type { AssessmentContextView, AssessmentReportPage, HistoryPage } from '../src/esportsAssessments/apiContracts'
import type { AssessmentReportView } from '../src/esportsAssessments/contracts'
import { pressOwnedDesktopEscape } from './helpers/desktopBrowserFocus'
import {
  SUITES, captureSafeEvidence, chooseFormal, choosePractice, chooseProject,
  exercisePcInput, expectFrozenV3TaskIdentity, expectPhase, finishTrial, loginAsAssessmentPreviewAdmin,
  observeNativeActiveRun, observeUiSave, openAssessmentHome, prepareTrial, readAssessmentJson, recordFailureContext,
  startMeasured,
} from './helpers/esportsAssessment'

test.describe.configure({ mode: 'serial' })
test.setTimeout(300_000)
test.afterEach(async ({ page }, info) => { await recordFailureContext(page, info) })
const pcSuites = ['pc-fps', 'pc-moba'] as const
const continuedPersistedItems = 8
const realItemBudgetMs = 70_000

test('EA-PC-REAL-001 real 60-second PC FPS and MOBA input, pointer lock, save and continue', async ({ page }, testInfo) => {
  // No Playwright clock is installed in this case: both formal items last real 60s.
  await loginAsAssessmentPreviewAdmin(page)
  for (const suite of pcSuites) {
    await openAssessmentHome(page)
    await chooseFormal(page, suite)
    const first = SUITES[suite].tests[0]
    await chooseProject(page, first)
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
    const nativeRun = await observeNativeActiveRun(page)
    try {
      await startMeasured(page, 'formal', undefined, 'pc_formal_countdown')
      await expectPhase(page, 'running', 'pc_formal_running')
      await exercisePcInput(page, suite, first)
      const saved = await save.waitSaved()
      expect(await nativeRun.activeDurationMs(), 'native finish must reach the fixed 60-second logical deadline').toBeGreaterThanOrEqual(60_000)
      expect(save.postCount()).toBe(1)
      expect(save.operationKeysAgree()).toBe(true)
      expect(save.inputResponded(first), 'several actual inputs must affect saved metrics').toBe(true)
      expect(saved.mode === 'formal' && saved.session.results.length === 1).toBe(true)
      await expect(page.getByRole('region', { name: '结果已保存', exact: true })).toBeVisible()
      await captureSafeEvidence(page, testInfo, { real_active_60s: true, mouse_input_responded: true, keyboard_trial_navigation: true, server_saved: true })
      await page.getByRole('button', { name: '继续下一项', exact: true }).click()
      await expect(page.getByText('已保存 1 / 4', { exact: true })).toBeVisible()
    } finally { save.dispose(); await nativeRun.dispose() }
  }
  const context = await readAssessmentJson<AssessmentContextView>(page, '/context')
  expect(context.active_run === null && context.own_in_progress_sessions.length === 2).toBe(true)
  expect(pcSuites.every(suite => context.own_in_progress_sessions.some(session => session.suite_id === suite && session.results.length === 1))).toBe(true)
})

test('EA-PC-REAL-002 real-time continuation of six formal items and two independent practices', async ({ page }, testInfo) => {
  test.setTimeout(continuedPersistedItems * realItemBudgetMs + 120_000)
  await loginAsAssessmentPreviewAdmin(page)
  for (const suite of pcSuites) {
    await openAssessmentHome(page)
    await chooseFormal(page, suite, { resume: true })
    const remaining = SUITES[suite].tests.slice(1)
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
        if (saved.mode === 'formal') expectFrozenV3TaskIdentity(saved.session.suite_config.tasks[item], suite, item)
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
    const nativeRun = await observeNativeActiveRun(page)
    try {
      await startMeasured(page, 'practice')
      await expectPhase(page, 'running')
      await exercisePcInput(page, suite, first)
      const saved = await save.waitSaved()
      expect(await nativeRun.activeDurationMs(), 'native finish must reach the fixed 60-second logical deadline').toBeGreaterThanOrEqual(60_000)
      expect(save.postCount()).toBe(1)
      expect(save.operationKeysAgree()).toBe(true)
      expect(saved.mode === 'practice' && saved.scored.score === null && saved.scored.grade === null && saved.session === null).toBe(true)
      expect(save.inputResponded(first)).toBe(true)
      await expect(page.getByRole('region', { name: '结果已保存', exact: true })).toContainText('不计入正式进度')
      await captureSafeEvidence(page, testInfo, { real_active_60s: true, separate_practice: true, reports_reached: true, server_saved: true })
    } finally { save.dispose(); await nativeRun.dispose() }
  }
  const formal = await readAssessmentJson<HistoryPage>(page, '/history?mode=formal&limit=100')
  const practice = await readAssessmentJson<HistoryPage>(page, '/history?mode=practice&limit=100')
  const reports = await readAssessmentJson<AssessmentReportPage>(page, '/reports?limit=100')
  expect(formal.visible_total === 2 && formal.items.length === 2 && formal.next_cursor === null).toBe(true)
  expect(pcSuites.every(suite => formal.items.some(item => item.mode === 'formal' && item.session.suite_id === suite && item.session.status === 'completed' && item.session.results.length === 4))).toBe(true)
  expect(practice.visible_total === 2 && practice.items.length === 2 && practice.next_cursor === null).toBe(true)
  expect(pcSuites.every(suite => practice.items.some(item => item.mode === 'practice' && item.suite_id === suite && item.report_id === null && item.scored.score === null))).toBe(true)
  for (const item of practice.items) {
    if (item.mode === 'practice') expectFrozenV3TaskIdentity(item.config, item.suite_id, item.test_id)
  }
  expect(reports.visible_total === 2 && reports.items.length === 2 && reports.next_cursor === null).toBe(true)
  expect(pcSuites.every(suite => reports.items.filter(item => item.suite_id === suite).length === 1)).toBe(true)
  for (const summary of reports.items) {
    const report = await readAssessmentJson<AssessmentReportView>(page, `/reports/${summary.id}`)
    expect(report.suite.catalog_version).toBe(`${summary.suite_id}-v3`)
    for (const result of report.results) expectFrozenV3TaskIdentity(result.config, summary.suite_id, result.test_id)
  }
})
