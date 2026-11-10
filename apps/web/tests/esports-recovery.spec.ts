import { expect, test, type Page, type Request, type Route } from '@playwright/test'
import type { AssessmentContextView, AttemptDetailView, HistoryPage, RecoveryPage } from '../src/esportsAssessments/apiContracts'
import type { AttemptView, ResultSaveView } from '../src/esportsAssessments/contracts'
import {
  SUITES, captureSafeEvidence, chooseFormal, choosePractice, chooseProject, exercisePcInput,
  expectCurrentTaskIdentity, expectPhase, finishTrial, isUiResultRequest, loginAsAssessmentPreviewAdmin,
  observeNativeActiveRun, observeUiSave, openAssessmentHome, prepareTrial, readAssessmentJson, recordFailureContext, startMeasured,
} from './helpers/esportsAssessment'

test.describe.configure({ mode: 'serial' })
test.setTimeout(150_000)
test.afterEach(async ({ page }, info) => { await recordFailureContext(page, info) })

/** Transport fault only: forward the untouched UI POST, then discard its response. */
async function losePersistedResponse(page: Page, mode: 'formal' | 'practice') {
  const segment = mode === 'formal' ? 'attempts' : 'practice-runs'
  const pattern = `**/api/v1/esports-assessments/${segment}/*/result`
  let handled = false
  let persisted = false
  let failed = false
  let attemptId = ''
  let operationKey = ''
  let resultId = ''
  const handler = async (route: Route) => {
    const request = route.request()
    if (handled || !isUiResultRequest(request, mode)) { await route.continue(); return }
    handled = true
    try {
      const body = request.postDataJSON() as { operation_key?: unknown }
      operationKey = typeof body.operation_key === 'string' ? body.operation_key : ''
      attemptId = new URL(request.url()).pathname.split('/').at(-2) ?? ''
      // No data/header override, no synthetic raw, no extra POST, no transport retry.
      const upstream = await route.fetch({ maxRetries: 0 })
      try {
        const saved = await upstream.json() as ResultSaveView
        persisted = upstream.ok() && saved.mode === mode && typeof saved.result_id === 'string'
        resultId = persisted ? saved.result_id : ''
      } finally { await upstream.dispose() }
    } catch { failed = true }
    finally { await route.abort('failed') }
  }
  await page.route(pattern, handler)
  return {
    async waitPersisted() {
      await expect.poll(() => persisted || failed, { timeout: 20_000, message: 'upstream UI submission must persist before response loss' }).toBe(true)
      expect(persisted && !failed && operationKey.length > 0).toBe(true)
      return { attemptId, operationKey, resultId }
    },
    dispose: () => page.unroute(pattern, handler),
  }
}

async function finishRealGridshot(page: Page, mode: 'formal' | 'practice') {
  const nativeRun = await observeNativeActiveRun(page)
  try {
    await startMeasured(page, mode)
    await expectPhase(page, 'running')
    await exercisePcInput(page, 'pc-fps', 'gridshot')
    expect(await nativeRun.activeDurationMs(), 'native finish must reach the fixed 60-second logical deadline').toBeGreaterThanOrEqual(60_000)
  } finally { await nativeRun.dispose() }
}

test('EA-REC-FORMAL-001 real-time persisted formal response loss reconciles the original operation after reload', async ({ page }, testInfo) => {
  await loginAsAssessmentPreviewAdmin(page)
  await openAssessmentHome(page)
  await chooseFormal(page, 'pc-fps')
  await chooseProject(page, 'gridshot')
  await prepareTrial(page)
  await finishTrial(page)
  const save = observeUiSave(page, 'formal')
  const fault = await losePersistedResponse(page, 'formal')
  try {
    await finishRealGridshot(page, 'formal')
    const persisted = await fault.waitPersisted()
    await expect(page.getByRole('region', { name: '恢复与保存', exact: true })).toBeVisible()
    // Read-only cache evidence: never inject/remove pending or print raw/operation IDs.
    const retainedOriginal = await page.evaluate(({ operationKey, attemptId }) => Object.keys(sessionStorage)
      .filter(key => key.startsWith('erp:esports-assessments:pending:'))
      .some(key => {
        try {
          const value = JSON.parse(sessionStorage.getItem(key) ?? 'null')
          return value?.operation_key === operationKey && value?.attempt_id === attemptId
        } catch { return false }
      }), persisted)
    expect(retainedOriginal).toBe(true)
    let uiDetailReads = 0
    let replacementStarts = 0
    const detailPath = `/api/v1/esports-assessments/attempts/${persisted.attemptId}`
    const countDetail = (request: Request) => {
      if (request.method() === 'GET' && new URL(request.url()).pathname === detailPath) uiDetailReads += 1
      if (request.method() === 'POST' && /\/esports-assessments\/sessions\/[a-f0-9-]+\/attempts$/.test(new URL(request.url()).pathname)) replacementStarts += 1
    }
    page.on('request', countDetail)
    try {
      await page.reload()
      await expect(page.getByRole('region', { name: '结果已保存', exact: true })).toBeVisible()
      expect(uiDetailReads > 0, 'the reloaded UI must reconcile through the same attempt detail GET').toBe(true)
      expect(replacementStarts, 'reconciliation must not allocate a replacement attempt').toBe(0)
    } finally { page.off('request', countDetail) }
    const originalCleared = await page.evaluate(({ operationKey, attemptId }) => !Object.keys(sessionStorage)
      .filter(key => key.startsWith('erp:esports-assessments:pending:'))
      .some(key => {
        try {
          const value = JSON.parse(sessionStorage.getItem(key) ?? 'null')
          return value?.operation_key === operationKey && value?.attempt_id === attemptId
        } catch { return false }
      }), persisted)
    expect(originalCleared, 'confirmed original pending must be cleared, not stranded by a completed-project route').toBe(true)
    const detail = await readAssessmentJson<AttemptDetailView>(page, `/attempts/${persisted.attemptId}`)
    expect(detail.attempt.state === 'submitted' && detail.saved?.result_id === persisted.resultId).toBe(true)
    expect(detail.saved?.mode === 'formal' && detail.saved.session.results.length === 1).toBe(true)
    expectCurrentTaskIdentity(detail.attempt.config, 'pc-fps', 'gridshot')
    expect(detail.saved?.scored.metrics.shots).toBe(5)
    expect(save.postCount(), 'reload must reconcile by GET, not resubmit').toBe(1)
    expect(save.operationKeysAgree()).toBe(true)
    await captureSafeEvidence(page, testInfo, { real_active_60s: true, upstream_response_lost: true, upstream_persisted: true, original_pending_retained: true, confirmed_pending_cleared: true, reload_get_reconciled: true, duplicate_save_absent: true })
    await page.getByRole('button', { name: '继续下一项', exact: true }).click()
    await expect(page.getByText(`已保存 1 / ${SUITES['pc-fps'].tests.length}`, { exact: true })).toBeVisible()
  } finally { save.dispose(); await fault.dispose() }
})

test('EA-REC-PRACTICE-002 real-time practice survives response loss and a fresh tab with no local pending', async ({ page, context }, testInfo) => {
  await loginAsAssessmentPreviewAdmin(page)
  await openAssessmentHome(page)
  await choosePractice(page, 'pc-fps', 'gridshot')
  await prepareTrial(page)
  await finishTrial(page)
  const fault = await losePersistedResponse(page, 'practice')
  let posts = 0
  const countPost = (request: Request) => { if (isUiResultRequest(request, 'practice')) posts += 1 }
  context.on('request', countPost)
  let fresh: Page | null = null
  try {
    await finishRealGridshot(page, 'practice')
    const persisted = await fault.waitPersisted()
    await expect(page.getByRole('region', { name: '恢复与保存', exact: true })).toBeVisible()
    // No opener means no sessionStorage copy. Cookies authenticate the same preview admin.
    fresh = await context.newPage()
    await fresh.goto(`/esports-assessments/practice?suite=pc-fps&test=gridshot&difficulty=3&practice_id=${persisted.attemptId}`)
    const pendingAbsent = await fresh.evaluate(() => !Object.keys(sessionStorage).some(key => key.startsWith('erp:esports-assessments:pending:')))
    expect(pendingAbsent).toBe(true)
    await page.close()
    await fresh.reload()
    await expect(fresh.getByRole('region', { name: '结果已保存', exact: true })).toBeVisible()
    const detail = await readAssessmentJson<AttemptDetailView>(fresh, `/practice-runs/${persisted.attemptId}`)
    expect(detail.attempt.state === 'submitted' && detail.saved?.result_id === persisted.resultId).toBe(true)
    expect(detail.saved?.mode === 'practice' && detail.saved.scored.score === null && detail.saved.scored.grade === null).toBe(true)
    expectCurrentTaskIdentity(detail.attempt.config, 'pc-fps', 'gridshot')
    expect(detail.saved?.scored.metrics.shots).toBe(5)
    expect(posts, 'fresh detail recovery must not submit a second practice').toBe(1)
    const history = await readAssessmentJson<HistoryPage>(fresh, '/history?mode=practice&limit=100')
    expect(history.visible_total === 1 && history.items.length === 1).toBe(true)
    await captureSafeEvidence(fresh, testInfo, { real_active_60s: true, upstream_response_lost: true, upstream_persisted: true, local_pending_absent: true, fresh_detail_reconciled: true, duplicate_save_absent: true })
  } finally {
    context.off('request', countPost)
    if (!page.isClosed()) await fault.dispose()
    await fresh?.close()
  }
})

test('EA-REC-MULTITAB-003 second tab only reconciles the held attempt and leaves it interrupted without another start', async ({ page, context }, testInfo) => {
  await loginAsAssessmentPreviewAdmin(page)
  // Create before the attempt, then restore the real foreground. Never suppress blur.
  const second = await context.newPage()
  await page.bringToFront()
  let starts = 0
  let resultPosts = 0
  const onRequest = (request: Request) => {
    if (request.method() === 'POST' && /\/esports-assessments\/sessions\/[a-f0-9-]+\/attempts$/.test(new URL(request.url()).pathname)) starts += 1
    if (isUiResultRequest(request, 'formal')) resultPosts += 1
  }
  context.on('request', onRequest)
  try {
    await openAssessmentHome(page)
    await chooseFormal(page, 'pc-fps', { resume: true })
    await chooseProject(page, 'sixshot')
    await prepareTrial(page)
    await finishTrial(page)
    const created = page.waitForResponse(response => response.request().method() === 'POST' && /\/esports-assessments\/sessions\/[a-f0-9-]+\/attempts$/.test(new URL(response.url()).pathname))
    await startMeasured(page, 'formal')
    const response = await created
    expect(response.ok()).toBe(true)
    const attempt = await response.json() as AttemptView
    expectCurrentTaskIdentity(attempt.config, 'pc-fps', 'sixshot')
    await expectPhase(page, 'running')
    const recoveryUrl = page.url()
    // Background navigation is deliberate. Some browsers still blur the first tab;
    // that legitimate interruption is accepted, never masked or replaced by a run.
    await second.goto(recoveryUrl)
    await expect(second.getByRole('region', { name: '测试场景', exact: true })).toBeVisible()
    const observed = await readAssessmentJson<AttemptDetailView>(second, `/attempts/${attempt.id}`)
    const realBlurInterrupted = observed.attempt.state === 'interrupted'
    if (!realBlurInterrupted) {
      expect(observed.attempt.state === 'running' || observed.attempt.state === 'awaiting_submission').toBe(true)
      await expect(second.getByRole('region', { name: '恢复与保存', exact: true })).toContainText('不能恢复或拼接计时')
      await expect(second.getByRole('button', { name: '开始正式测试', exact: true })).toHaveCount(0)
      await second.getByRole('button', { name: '结束未完成尝试并重新开始', exact: true }).click()
    } else {
      await expectPhase(page, 'interrupted')
      await expect(page.getByRole('alert')).toBeVisible()
    }
    await expect.poll(async () => (await readAssessmentJson<AttemptDetailView>(second, `/attempts/${attempt.id}`)).attempt.state === 'interrupted').toBe(true)
    expect(starts, 'second tab cannot allocate a replacement attempt').toBe(1)
    expect(resultPosts).toBe(0)
    const state = await readAssessmentJson<AssessmentContextView>(second, '/context')
    const recovery = await readAssessmentJson<RecoveryPage>(second, '/recovery?limit=100')
    const formal = await readAssessmentJson<HistoryPage>(second, '/history?mode=formal&limit=100')
    const practice = await readAssessmentJson<HistoryPage>(second, '/history?mode=practice&limit=100')
    expect(state.active_run === null && recovery.visible_total === 0 && recovery.items.length === 0).toBe(true)
    expect(state.own_in_progress_sessions.length === 1 && state.own_in_progress_sessions[0].results.length === 1).toBe(true)
    expect(formal.visible_total === 1 && formal.items[0]?.mode === 'formal' && formal.items[0].session.report_id === null).toBe(true)
    expect(practice.visible_total === 1).toBe(true)
    await captureSafeEvidence(second, testInfo, { original_attempt_interrupted: true, no_second_start: true, no_result_duplicate: true, real_blur_interrupted: realBlurInterrupted, active_attempt_absent: true })
  } finally { context.off('request', onRequest); await second.close() }
})
