import { mkdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { expect, test, type BrowserContext, type Locator, type Page, type Request, type Response, type TestInfo } from '@playwright/test'
import { loginWithIdentifier } from '../helpers'
import { e2eCredentialTuple } from '../e2eCredentials'
import type { ResultSaveView, TaskConfig } from '../../src/esportsAssessments/contracts'
import { nativeActiveDuration } from './esportsTiming'
import { activateOwnedDesktopBrowser } from './desktopBrowserFocus'
import { validArtifactIdentity } from './esportsArtifactIdentity'
import { readHostInterruption } from './esportsInterruption'
import { AssessmentTestClock } from './esportsClock'
import { projectSelection, type CatalogRevision, type TestId } from './esportsSelection'
import { esportsOwnedConfig } from '../esportsOwnedConfig'
import { readPcPreflight, validatePcPreflight } from './esportsPreflightDiagnostic.mjs'
import { beginPcAcquisition, finishPcAcquisition } from './esportsAcquisitionDiagnostic.mjs'
import { hasCapabilityDiagnostic, markCapabilityCase, observeCapabilitySelection } from './esportsCapabilityDiagnostic.mjs'

export { TEST_LABELS, type TestId } from './esportsSelection'
export const SUITES = {
  'pc-fps': { label: '端游 FPS', tests: ['precision', 'tracking', 'switching', 'recognition'] },
  'pc-moba': { label: '端游 MOBA', tests: ['last-hit', 'orb-walk', 'dodge', 'map-awareness'] },
  'mobile-fps': { label: '手游 FPS', tests: ['precision', 'tracking', 'switching', 'move-fire'] },
  'mobile-moba': { label: '手游 MOBA', tests: ['dodge', 'skill-aim', 'move-attack', 'map-awareness'] },
} as const
export type Suite = keyof typeof SUITES
export type Activate = (locator: Locator) => Promise<void>
const click: Activate = locator => locator.click()
const simulated = new AssessmentTestClock()
type BrowserEvents = { console_error_seen: boolean; page_error_seen: boolean; request_failure_seen: boolean }
const browserEvents = new WeakMap<Page, BrowserEvents>()
const observedContexts = new WeakSet<BrowserContext>()

export function expectFrozenV3TaskIdentity(config: TaskConfig, suite: Suite, testId: TestId): void {
  const catalogVersion = `${suite}-v3`
  const usesV2ResultRules = testId === 'switching' || testId === 'move-fire' || testId === 'dodge'
  const resultVersion = `${suite}-v${usesV2ResultRules ? 2 : 1}`
  expect(config.suite_id).toBe(suite)
  expect(config.test_id).toBe(testId)
  expect(config.catalog_version).toBe(catalogVersion)
  expect(config.engine_version).toBe(catalogVersion)
  expect(config.scoring_version).toBe(resultVersion)
  expect(config.report_version).toBe(resultVersion)
}

function observeBrowserEvents(page: Page): void {
  if (browserEvents.has(page)) return
  const events: BrowserEvents = { console_error_seen: false, page_error_seen: false, request_failure_seen: false }
  browserEvents.set(page, events)
  page.on('console', message => { if (message.type() === 'error') events.console_error_seen = true })
  page.on('pageerror', () => { events.page_error_seen = true })
  page.on('requestfailed', () => { events.request_failure_seen = true })
  // Never collect message text, stack traces, request bodies or URLs.
}

export const host = (page: Page): Locator => page.getByRole('region', { name: '测试场景', exact: true })
export const canvas = (page: Page, suite: Suite): Locator => page.getByLabel(suite.endsWith('fps') ? 'FPS 测试场景' : 'MOBA 测评场景', { exact: true })

const HOST_PHASES = ['intro', 'preflight', 'trial', 'starting', 'countdown', 'running', 'pending', 'saved', 'interrupted'] as const
type HostPhase = typeof HOST_PHASES[number]
const PHASE_CHECKPOINTS = ['pc_initial_trial', 'pc_pointer_lock_exit', 'pc_reentry_trial', 'pc_trial_finish', 'pc_formal_countdown', 'pc_formal_running'] as const
export type PhaseCheckpoint = typeof PHASE_CHECKPOINTS[number]

async function annotatePhaseFailure(page: Page, expected: HostPhase, checkpoint: PhaseCheckpoint): Promise<void> {
  const info = test.info()
  if (info.annotations.some(annotation => annotation.type === 'esports.phase.v1')
    || !HOST_PHASES.includes(expected) || !PHASE_CHECKPOINTS.includes(checkpoint)) return
  const observation = await page.evaluate(phases => {
    let observed = 'unknown'
    let pointer_lock = false
    let document_focus = false
    let webgl2: boolean
    try {
      const target = document.querySelector('[aria-label="测试场景"][data-host-phase]')
      const value = target?.getAttribute('data-host-phase')
      observed = !target ? 'missing' : value && phases.includes(value) ? value : 'unknown'
    } catch { /* Diagnostic failure must not replace the phase assertion. */ }
    try { pointer_lock = document.pointerLockElement !== null } catch { /* false means not confirmed */ }
    try { document_focus = document.hasFocus() } catch { /* false means not confirmed */ }
    // Probe creation capability only, never request a context on the business canvas.
    // This executes after failure, not in any successful trial/running path.
    let probe: HTMLCanvasElement | null = null
    try {
      probe = document.createElement('canvas')
      const context = probe.getContext('webgl2')
      webgl2 = context !== null
      context?.getExtension('WEBGL_lose_context')?.loseContext()
    } catch { webgl2 = false }
    finally { if (probe) { probe.width = 0; probe.height = 0 } }
    return { observed, pointer_lock, document_focus, webgl2 }
  }, [...HOST_PHASES] as string[]).catch(() => null)
  const observed = observation?.observed
  info.annotations.push({ type: 'esports.phase.v1', description: JSON.stringify({
    checkpoint,
    expected,
    observed: observed === 'missing' || (HOST_PHASES as readonly string[]).includes(observed ?? '') ? observed : 'unknown',
    pointer_lock: observation?.pointer_lock === true,
    document_focus: observation?.document_focus === true,
    webgl2: observation?.webgl2 === true,
  }) })
}

export async function expectPhase(page: Page, phase: HostPhase, checkpoint?: PhaseCheckpoint): Promise<void> {
  try {
    await expect(host(page)).toHaveAttribute('data-host-phase', phase, { timeout: 15_000 })
    if (phase === 'interrupted') markCapabilityCase(page, 'interrupted_ui')
  } catch (error) {
    try {
      const interruption = await page.evaluate(readHostInterruption)
      await captureSafeEvidence(page, test.info(), { phase_assertion_failed: true, ...interruption })
    } catch { /* Preserve the assertion, never replace it with a diagnostic failure. */ }
    if (checkpoint) {
      try { await annotatePhaseFailure(page, phase, checkpoint) } catch { /* Preserve the original assertion failure. */ }
      if (checkpoint === 'pc_initial_trial' && esportsOwnedConfig()) {
        try {
          const diagnostic = validatePcPreflight(await page.evaluate(readPcPreflight, undefined))
          if (diagnostic) test.info().annotations.push({ type: 'esports.preflight.v1', description: JSON.stringify(diagnostic) })
        } catch { /* Diagnostic unavailability never changes the original failure. */ }
      }
    }
    throw error
  }
}

export async function recordFailureContext(page: Page, info: TestInfo): Promise<void> {
  if (info.status === info.expectedStatus) return
  try { await captureSafeEvidence(page, info, { case_failed: true, ...await page.evaluate(readHostInterruption) }) }
  catch { /* A closed page cannot provide evidence; preserve the actual test failure. */ }
}

export interface NativeActiveRunObservation {
  activeDurationMs(): Promise<number>
  dispose(): Promise<void>
}

/** Observe Host-owned native boundaries, not the lagging React phase commit times. */
export async function observeNativeActiveRun(page: Page): Promise<NativeActiveRunObservation> {
  await page.evaluate(() => {
    type Observation = {
      observer: MutationObserver
      timing: { startedAt: number; deadline: number; finishedAt: number } | null
    }
    type ObservedWindow = Window & typeof globalThis & { __esportsNativeRun?: Observation }
    const observed = window as ObservedWindow
    observed.__esportsNativeRun?.observer.disconnect()
    const target = document.querySelector<HTMLElement>('[aria-label="测试场景"][data-host-phase]')
    if (!target) throw new Error('assessment host is unavailable')
    const observation: Observation = { observer: null!, timing: null }
    const observer = new MutationObserver(() => {
      const values = ['data-run-start-ms', 'data-run-deadline-ms', 'data-run-finish-ms'].map(key => target.getAttribute(key))
      if (values.every(value => value !== null && value !== '')) {
        const [startedAt, deadline, finishedAt] = values.map(Number)
        observation.timing = { startedAt, deadline, finishedAt }
      }
    })
    observer.observe(target, { attributes: true, attributeFilter: ['data-run-start-ms', 'data-run-deadline-ms', 'data-run-finish-ms'] })
    observation.observer = observer
    observed.__esportsNativeRun = observation
  })
  return {
    activeDurationMs: async () => {
      await page.waitForFunction(() => Boolean((window as Window & { __esportsNativeRun?: { timing: unknown } }).__esportsNativeRun?.timing))
      const timing = await page.evaluate(() => {
        type Observation = { timing: { startedAt: number; deadline: number; finishedAt: number } | null }
        const timing = (window as Window & { __esportsNativeRun?: Observation }).__esportsNativeRun?.timing
        if (!timing) throw new Error('native active-run boundaries are incomplete')
        return timing
      })
      return nativeActiveDuration(timing)
    },
    dispose: () => page.evaluate(() => {
      type Observation = { observer: MutationObserver }
      const observed = window as Window & typeof globalThis & { __esportsNativeRun?: Observation }
      observed.__esportsNativeRun?.observer.disconnect()
      delete observed.__esportsNativeRun
    }),
  }
}

export async function loginAsAssessmentPreviewAdmin(page: Page): Promise<void> {
  observeBrowserEvents(page)
  if (!observedContexts.has(page.context())) {
    observedContexts.add(page.context())
    page.context().on('page', observeBrowserEvents)
  }
  await loginWithIdentifier(page, ...e2eCredentialTuple('admin'))
  await activateOwnedDesktopBrowser(page, test.info().project.name)
}

async function activateAvailableSelection(locator: Locator, activate: Activate, phase: 'suite' | 'project' = 'suite'): Promise<void> {
  const diagnostic = hasCapabilityDiagnostic(locator.page())
  if (diagnostic) await observeCapabilitySelection(locator, phase, 'begin')
  try {
    await expect(locator, 'assessment preview selection must be visible').toBeVisible({ timeout: 15_000 })
    await expect(locator, 'assessment preview selection must be enabled').toBeEnabled({ timeout: 15_000 })
    await activate(locator)
    if (diagnostic) await observeCapabilitySelection(locator, phase, 'passed')
  } catch (error) {
    if (diagnostic) {
      try { await observeCapabilitySelection(locator, phase, 'failed') } catch { /* Preserve original assertion. */ }
    }
    throw error
  }
}

export async function openAssessmentHome(page: Page, activate: Activate = click): Promise<void> {
  const entry = page.getByRole('link', { name: '天赋评测 2', exact: true })
  const more = page.getByRole('button', { name: '更多', exact: true })
  await expect(entry.or(more).first()).toBeVisible()
  if (await more.isVisible()) await activate(more)
  await activate(page.getByRole('link', { name: '天赋评测 2', exact: true }))
  await expect(page.getByRole('heading', { name: '天赋评测 2', exact: true })).toBeVisible()
}

export async function chooseFormal(page: Page, suite: Suite, options: { resume?: boolean; activate?: Activate } = {}): Promise<void> {
  const activate = options.activate ?? click
  const selection = options.resume
    ? page.getByRole('link', { name: `继续 ${SUITES[suite].label}`, exact: true })
    : page.getByRole('button', { name: `${SUITES[suite].label} 正式测试`, exact: true })
  await activateAvailableSelection(selection, activate)
  await expect(page.getByRole('heading', { name: '正式评测项目', exact: true })).toBeVisible()
}

export async function chooseProject(page: Page, test: TestId, activate: Activate = click, catalogRevision: CatalogRevision = 3): Promise<void> {
  await activateAvailableSelection(page.getByRole('button', projectSelection(test, catalogRevision)), activate, 'project')
  await expect(host(page)).toBeVisible()
}

export async function choosePractice(page: Page, suite: Suite, test: TestId, options: { difficulty?: number; activate?: Activate; catalogRevision?: CatalogRevision } = {}): Promise<void> {
  const activate = options.activate ?? click
  await activateAvailableSelection(page.getByRole('button', { name: `${SUITES[suite].label} 自由练习`, exact: true }), activate)
  await expect(page.getByRole('heading', { name: '自由练习', exact: true })).toBeVisible()
  await page.getByRole('combobox', { name: '练习难度', exact: true }).selectOption(String(options.difficulty ?? 3))
  await chooseProject(page, test, activate, options.catalogRevision)
}

/** Install before login/navigation. This never changes production constants. */
export async function installSimulatedClock(page: Page): Promise<void> {
  await simulated.install(page)
}

async function pauseBeforeTrial(page: Page): Promise<void> {
  await simulated.pauseBeforeTrial(page)
}

export async function advanceSimulated(page: Page, milliseconds: number): Promise<void> {
  // runFor executes every RAF/timer. fastForward would create an invalid frame gap.
  await simulated.advance(page, milliseconds)
}

export async function prepareTrial(page: Page, activate: Activate = click, checkpoint?: PhaseCheckpoint): Promise<void> {
  await pauseBeforeTrial(page)
  let diagnostic = false
  if (checkpoint === 'pc_initial_trial' && esportsOwnedConfig()) {
    try { diagnostic = await beginPcAcquisition(page, test.info()) } catch { /* Preserve original input/assertion. */ }
  }
  try {
    await activate(page.getByRole('button', { name: '检查设备并进入试练', exact: true }))
    await expectPhase(page, 'trial', checkpoint)
  } catch (error) {
    if (diagnostic) {
      try {
        const value = await finishPcAcquisition(page, true)
        if (value) test.info().annotations.push({ type: 'esports.acquisition.v1', description: JSON.stringify(value) })
      } catch { /* Unavailable diagnostics never replace the original failure. */ }
    }
    throw error
  } finally {
    if (diagnostic) {
      try { await finishPcAcquisition(page, false) } catch { /* Read-only listener cleanup. */ }
    }
  }
}

export async function finishTrial(page: Page, activate: Activate = click, checkpoint?: PhaseCheckpoint): Promise<void> {
  const button = page.getByRole('button', { name: '结束试练', exact: true })
  // Read-only native capability observation; all activation below is real input.
  const locked = await page.evaluate(() => document.pointerLockElement !== null)
  if (locked) {
    let focused = false
    for (let index = 0; index < 80; index += 1) {
      if (await button.evaluate(element => element === document.activeElement)) { focused = true; break }
      await page.keyboard.press('Tab')
    }
    expect(focused, 'trial finish must be keyboard reachable while pointer locked').toBe(true)
    await page.keyboard.press('Enter')
  } else await activate(button)
  await expectPhase(page, 'preflight', checkpoint)
}

/** Resolves at countdown, never advances time while the start request is pending. */
export async function startMeasured(page: Page, mode: 'formal' | 'practice', activate: Activate = click, checkpoint?: PhaseCheckpoint): Promise<void> {
  await activate(page.getByRole('button', { name: mode === 'formal' ? '开始正式测试' : '开始练习', exact: true }))
  await expectPhase(page, 'countdown', checkpoint)
}

async function inputGap(page: Page, milliseconds: number): Promise<void> {
  if (simulated.has(page)) await advanceSimulated(page, milliseconds)
  else await page.waitForTimeout(milliseconds)
}

/** Visible canvas coordinates only; no target/seed/engine introspection. */
export async function exercisePcInput(page: Page, suite: Suite, test: TestId): Promise<void> {
  if (!suite.startsWith('pc-')) throw new Error('PC input helper does not synthesize touch')
  const box = await canvas(page, suite).boundingBox()
  if (!box) throw new Error('assessment canvas is not visible')
  if (suite === 'pc-fps') {
    for (let index = 0; index < 4; index += 1) {
      await page.mouse.move(box.x + box.width * (0.48 + index * 0.01), box.y + box.height * 0.5, { steps: 3 })
      if (test !== 'tracking') await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.5)
      await inputGap(page, 260)
    }
  } else {
    // Last-hit's visibly adjacent minion is at (590, 281.25) in the 1000×562.5 arena.
    // Other scenes receive ordinary attack/move and point inputs, not guaranteed hits.
    for (let index = 0; index < 4; index += 1) {
      await page.mouse.click(box.x + box.width * 0.59, box.y + box.height * 0.5, { button: 'right' })
      if (test === 'map-awareness') await page.mouse.click(box.x + box.width * 0.84, box.y + box.height * 0.16)
      await inputGap(page, 1_100)
    }
  }
}

/** GET only, through the same authenticated browser context and local web proxy. */
export async function readAssessmentJson<T = unknown>(page: Page, relativePath: string): Promise<T> {
  if (!/^\/(?:context|history|recovery|reports(?:\/[a-f0-9-]+)?|sessions\/[a-f0-9-]+|attempts\/[a-f0-9-]+|practice-runs\/[a-f0-9-]+)(?:\?[^#]*)?$/.test(relativePath)) {
    throw new Error('assessment evidence path is not allowlisted')
  }
  const response = await page.request.get(`/api/v1/esports-assessments${relativePath}`)
  if (!response.ok()) throw new Error('assessment GET evidence is unavailable')
  try { return await response.json() as T }
  catch { throw new Error('assessment GET evidence is malformed') }
  finally { await response.dispose() }
}

export function isUiResultRequest(request: Request, mode: 'formal' | 'practice'): boolean {
  const segment = mode === 'formal' ? 'attempts' : 'practice-runs'
  return request.method() === 'POST' && new RegExp(`/api/v1/esports-assessments/${segment}/[a-f0-9-]+/result$`).test(new URL(request.url()).pathname)
}

export function observeUiSave(page: Page, mode: 'formal' | 'practice') {
  let count = 0
  const keys: string[] = []
  let saved: ResultSaveView | null = null
  let malformed = false
  const onRequest = (request: Request) => {
    if (!isUiResultRequest(request, mode)) return
    count += 1
    try {
      const body = request.postDataJSON() as { operation_key?: unknown }
      if (typeof body.operation_key === 'string') keys.push(body.operation_key)
      else malformed = true
    } catch { malformed = true }
  }
  const onResponse = async (response: Response) => {
    if (!isUiResultRequest(response.request(), mode) || !response.ok()) return
    try {
      const value = await response.json() as ResultSaveView
      if (value.mode !== mode || !value.scored || !value.result_id) malformed = true
      else saved = value
    } catch { malformed = true }
  }
  page.on('request', onRequest)
  page.on('response', onResponse)
  return {
    postCount: () => count,
    operationKeysAgree: () => !malformed && keys.length === count && new Set(keys).size === 1,
    async waitSaved(): Promise<ResultSaveView> {
      await expect.poll(() => saved !== null, { timeout: 90_000, message: 'UI result submission must be confirmed by server' }).toBe(true)
      if (!saved || malformed) throw new Error('invalid UI save evidence')
      return saved
    },
    inputResponded(test: TestId): boolean {
      const metrics = saved?.scored.metrics ?? {}
      if (test === 'precision') return typeof metrics.shots === 'number' && metrics.shots >= 3
      if (test === 'last-hit') return ['successes', 'early_attacks', 'late_attacks'].reduce((sum, key) => sum + (metrics[key] ?? 0), 0) >= 3
      if (test === 'move-fire') return typeof metrics.shots === 'number' && metrics.shots >= 3
        && typeof metrics.reached === 'number' && typeof metrics.joint_successes === 'number'
      if (test === 'move-attack' || test === 'orb-walk') return (metrics.attacks_completed ?? 0) + (metrics.cancelled_attacks ?? 0) >= 3
      if (test === 'skill-aim') return (metrics.released ?? 0) >= 3
      if (test === 'map-awareness') return (metrics.main_hits ?? 0) + (metrics.unprompted_count ?? 0) + (metrics.map_wrong ?? 0) >= 3
      return false
    },
    dispose() { page.off('request', onRequest); page.off('response', onResponse) },
  }
}

/** Only explicit non-identifying region captures and fixed boolean categories escape the runner. */
export async function captureSafeEvidence(page: Page, testInfo: TestInfo, categories: Record<string, boolean>): Promise<void> {
  const directory = process.env.ESPORTS_E2E_ARTIFACT_DIR
  const caseId = testInfo.title.split(' ')[0]
  if (!directory || !validArtifactIdentity(directory, caseId, categories)) {
    throw new Error('safe assessment artifact identity is unavailable')
  }
  await mkdir(directory, { recursive: true })
  const prefix = `${testInfo.project.name}-${caseId}`
  // Never capture the account header, report identity, raw snapshots or private URLs.
  const safeRegion = page.locator('canvas[aria-label="FPS 测试场景"], canvas[aria-label="MOBA 测评场景"]').first()
  if (!esportsOwnedConfig() && await safeRegion.isVisible()) await safeRegion.screenshot({ path: join(directory, `${prefix}-scene.png`) })
  const events = browserEvents.get(page)
  if (!events) throw new Error('browser event observation was not installed')
  // Native observations win over caller categories, including expected recovery faults.
  await writeFile(join(directory, `${prefix}-browser-events.json`), JSON.stringify({ ...categories, ...events }, null, 2))
}
