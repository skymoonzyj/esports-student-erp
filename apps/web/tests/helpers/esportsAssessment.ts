import { mkdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { expect, test, type BrowserContext, type Locator, type Page, type Request, type Response, type TestInfo } from '@playwright/test'
import { loginWithIdentifier } from '../helpers'
import { e2eCredentialTuple } from '../e2eCredentials'
import type { ResultSaveView, TaskConfig } from '../../src/esportsAssessments/contracts'
import { fixtureV4Config } from '../../src/esportsAssessments/testSupport/fixturesV4'
import { fixtureV5Config } from '../../src/esportsAssessments/testSupport/fixturesV5'
import { fixtureMobaV4Config } from '../../src/esportsAssessments/testSupport/fixturesMobaV4'
import { fixtureMobaV5Config } from '../../src/esportsAssessments/testSupport/fixturesMobaV5'
import { laneWorldToScreen } from '../../src/esportsAssessments/lane/projection'
import { nativeActiveDuration } from './esportsTiming'
import { activateOwnedDesktopBrowser } from './desktopBrowserFocus'
import { validArtifactIdentity, type SafeEvidencePhase } from './esportsArtifactIdentity'
import { readHostInterruption } from './esportsInterruption'
import { BoundedNetworkDiagnostics } from './esportsNetworkDiagnostics'
import { AssessmentTestClock } from './esportsClock'
import { projectSelection, type CatalogRevision, type TestId } from './esportsSelection'

export { TEST_LABELS, type TestId } from './esportsSelection'
export const SUITES = {
  'pc-fps': { label: '端游 FPS', tests: ['gridshot', 'sixshot', 'spheretrack90'] },
  'pc-moba': { label: '端游 MOBA', tests: ['last-hit'] },
  'mobile-fps': { label: '手游 FPS', tests: ['precision', 'tracking', 'switching', 'move-fire'] },
  'mobile-moba': { label: '手游 MOBA', tests: ['dodge', 'skill-aim', 'move-attack', 'map-awareness'] },
} as const
export type Suite = keyof typeof SUITES
export type Activate = (locator: Locator) => Promise<void>
const click: Activate = locator => locator.click()
const simulated = new AssessmentTestClock()
type BrowserEvents = { console_error_seen: boolean; page_error_seen: boolean; request_failure_seen: boolean }
const browserEvents = new WeakMap<Page, BrowserEvents>()
const networkDiagnostics = new WeakMap<Page, BoundedNetworkDiagnostics>()
const observedContexts = new WeakSet<BrowserContext>()

export function currentCatalogVersion(suite: Suite): string {
  return suite === 'pc-fps' ? 'pc-fps-v5' : suite === 'pc-moba' ? 'pc-moba-v5' : `${suite}-v3`
}

/** Canonical exact configuration, not merely an accepted version prefix. */
export function expectCurrentTaskIdentity(config: TaskConfig | undefined, suite: Suite, testId: TestId): void {
  if (suite === 'pc-fps') {
    if (testId !== 'gridshot' && testId !== 'sixshot' && testId !== 'spheretrack90') throw new Error('current PC FPS task is not a triad member')
    expect(config).toEqual(fixtureV5Config(testId))
  } else if (suite === 'pc-moba') {
    if (!config || config.params.kind !== 'lane_last_hit_towers' || testId !== 'last-hit') throw new Error('current PC MOBA requires the signed tower lane mode')
    expect(config).toEqual(fixtureMobaV5Config(config.params.attack_mode))
  } else expect(config).toEqual(fixtureV4Config(suite, testId))
}

/** Historical native fixtures remain exact v4; never alias them to current rules. */
export function expectFrozenMobaV4TaskIdentity(config: TaskConfig | undefined): void {
  if (!config || config.params.kind !== 'lane_last_hit') throw new Error('frozen v4 lane configuration is missing')
  expect(config).toEqual(fixtureMobaV4Config(config.params.attack_mode))
}

export function expectFrozenV3TaskIdentity(config: TaskConfig | undefined, suite: Suite, testId: TestId): void {
  if (!config) throw new Error('frozen v3 task configuration is missing')
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
  const diagnostics = new BoundedNetworkDiagnostics()
  networkDiagnostics.set(page, diagnostics)
  page.on('console', message => { if (message.type() === 'error') { events.console_error_seen = true; diagnostics.recordConsole(message.location().url) } })
  page.on('pageerror', () => { events.page_error_seen = true })
  page.on('requestfailed', request => { events.request_failure_seen = true; diagnostics.recordFailure(request.url(), request.method(), request.failure()?.errorText ?? '') })
  page.on('response', response => diagnostics.recordResponse(response.url(), response.request().method(), response.status()))
  // Never collect message text, stack traces, request bodies or URLs.
}

export const host = (page: Page): Locator => page.getByRole('region', { name: '测试场景', exact: true })
export const canvas = (page: Page, suite: Suite): Locator => page.getByLabel(suite.endsWith('fps') ? 'FPS 测试场景' : suite === 'pc-moba' ? /^MOBA (?:兵线)?测评场景$/ : 'MOBA 测评场景', { exact: true })

export interface LaneLifeFeedback { health: number; max_health: number; deaths: number; respawn_seconds: number | null }

/** Only values already visible in the public HUD; never engine/React/private state. */
export function parseLaneLifeFeedback(text: string): LaneLifeFeedback {
  const life = text.match(/生命 (\d+) \/ (\d+)/)
  const deaths = text.match(/死亡 (\d+)/)
  const respawn = text.match(/复活倒计时 (\d+) 秒/)
  if (!life || !deaths) throw new Error('visible lane life feedback is unavailable')
  const result = { health: Number(life[1]), max_health: Number(life[2]), deaths: Number(deaths[1]), respawn_seconds: respawn ? Number(respawn[1]) : null }
  if (result.max_health !== 1000 || result.health > result.max_health || (result.health === 0) !== (result.respawn_seconds !== null)) throw new Error('visible lane life feedback is inconsistent')
  return result
}

interface VisibleBox { x: number; y: number; width: number; height: number }
export interface LaneHudBounds {
  viewport: { width: number; height: number }
  feedback: VisibleBox
  cells: Array<{ box: VisibleBox; text: VisibleBox }>
}

/** Check actual rendered cell AND text bounds, not just the outer clipped box. */
export function expectLaneHudBounds(layout: LaneHudBounds): void {
  const inside = (inner: VisibleBox, outer: VisibleBox) => {
    expect(inner.width).toBeGreaterThan(0)
    expect(inner.height).toBeGreaterThan(0)
    expect(inner.x).toBeGreaterThanOrEqual(outer.x - .5)
    expect(inner.y).toBeGreaterThanOrEqual(outer.y - .5)
    expect(inner.x + inner.width).toBeLessThanOrEqual(outer.x + outer.width + .5)
    expect(inner.y + inner.height).toBeLessThanOrEqual(outer.y + outer.height + .5)
  }
  expect(layout.feedback.height).toBe(64)
  expect(layout.cells).toHaveLength(8)
  inside(layout.feedback, { x: 0, y: 0, ...layout.viewport })
  for (const cell of layout.cells) { inside(cell.box, layout.feedback); inside(cell.text, cell.box) }
}

export async function observeLaneHudBounds(page: Page): Promise<LaneHudBounds> {
  return page.getByLabel('兵线实时反馈', { exact: true }).evaluate(element => {
    const box = (rect: DOMRect) => ({ x: rect.x, y: rect.y, width: rect.width, height: rect.height })
    return { viewport: { width: window.innerWidth, height: window.innerHeight }, feedback: box(element.getBoundingClientRect()), cells: [...element.children].map(cell => {
      const range = document.createRange()
      range.selectNodeContents(cell)
      return { box: box(cell.getBoundingClientRect()), text: box(range.getBoundingClientRect()) }
    }) }
  })
}

export interface StageLayoutObservation {
  canvas: { x: number; y: number; width: number; height: number }
  host: { x: number; y: number }
  window_scroll: { x: number; y: number }
  ancestor_scroll: Array<{ tag: string; left: number; top: number }>
}

export function expectStableStageLayout(before: StageLayoutObservation, after: StageLayoutObservation): void {
  const relative = (observation: StageLayoutObservation) => ({ ...observation.canvas,
    x: observation.canvas.x - observation.host.x, y: observation.canvas.y - observation.host.y })
  expect(relative(after), `stage layout before/after (host-relative; includes scroll offsets): ${JSON.stringify({ before, after })}`).toEqual(relative(before))
}

export async function observeStageLayout(page: Page): Promise<StageLayoutObservation> {
  return canvas(page, 'pc-fps').evaluate(element => {
    const hostElement = element.closest('[aria-label="测试场景"]')
    if (!hostElement) throw new Error('assessment canvas has no owning Host')
    const rect = element.getBoundingClientRect(), hostRect = hostElement.getBoundingClientRect()
    const ancestor_scroll: StageLayoutObservation['ancestor_scroll'] = []
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      if (parent.scrollHeight > parent.clientHeight || parent.scrollWidth > parent.clientWidth) {
        ancestor_scroll.push({ tag: parent.tagName, left: parent.scrollLeft, top: parent.scrollTop })
      }
    }
    return { canvas: { x: rect.x, y: rect.y, width: rect.width, height: rect.height }, host: { x: hostRect.x, y: hostRect.y },
      window_scroll: { x: window.scrollX, y: window.scrollY }, ancestor_scroll }
  })
}

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
  } catch (error) {
    try {
      const interruption = await page.evaluate(readHostInterruption)
      await captureSafeEvidence(page, test.info(), { phase_assertion_failed: true, ...interruption })
    } catch { /* Preserve the assertion, never replace it with a diagnostic failure. */ }
    if (checkpoint) {
      try { await annotatePhaseFailure(page, phase, checkpoint) } catch { /* Preserve the original assertion failure. */ }
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
export async function observeNativeActiveRun(page: Page, durationMs: 60000 | 300000 = 60000): Promise<NativeActiveRunObservation> {
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
      return nativeActiveDuration(timing, durationMs)
    },
    dispose: () => page.evaluate(() => {
      type Observation = { observer: MutationObserver }
      const observed = window as Window & typeof globalThis & { __esportsNativeRun?: Observation }
      observed.__esportsNativeRun?.observer.disconnect()
      delete observed.__esportsNativeRun
    }),
  }
}

async function loginAsAssessmentAccount(page: Page, key: 'admin' | 'student'): Promise<void> {
  observeBrowserEvents(page)
  if (!observedContexts.has(page.context())) {
    observedContexts.add(page.context())
    page.context().on('page', observeBrowserEvents)
  }
  await loginWithIdentifier(page, ...e2eCredentialTuple(key))
  await activateOwnedDesktopBrowser(page, test.info().project.name)
}
export async function loginAsAssessmentPreviewAdmin(page: Page): Promise<void> { await loginAsAssessmentAccount(page, 'admin') }
export async function loginAsAssessmentParticipant(page: Page): Promise<void> { await loginAsAssessmentAccount(page, 'student') }

async function activateAvailableSelection(locator: Locator, activate: Activate): Promise<void> {
  await expect(locator, 'assessment preview selection must be visible').toBeVisible({ timeout: 15_000 })
  await expect(locator, 'assessment preview selection must be enabled').toBeEnabled({ timeout: 15_000 })
  await activate(locator)
}

export async function openAssessmentHome(page: Page, activate: Activate = click): Promise<void> {
  const entry = page.getByRole('link', { name: '天赋评测 2', exact: true })
  const more = page.getByRole('button', { name: '更多', exact: true })
  await expect(entry.or(more).first()).toBeVisible()
  if (await more.isVisible()) await activate(more)
  await activate(page.getByRole('link', { name: '天赋评测 2', exact: true }))
  await expect(page.getByRole('heading', { name: '天赋评测 2', exact: true })).toBeVisible()
}

export async function chooseFormal(page: Page, suite: Suite, options: { resume?: boolean; activate?: Activate; attack_mode?: 'melee' | 'ranged' } = {}): Promise<void> {
  const activate = options.activate ?? click
  if (suite === 'pc-moba' && !options.resume) await page.getByRole('combobox', { name: '端游 MOBA 人物模式' }).selectOption(options.attack_mode ?? 'melee')
  const selection = options.resume
    ? page.getByRole('link', { name: `继续 ${SUITES[suite].label}`, exact: true })
    : page.getByRole('button', { name: `${SUITES[suite].label} 正式测试`, exact: true })
  await activateAvailableSelection(selection, activate)
  await expect(page.getByRole('heading', { name: '正式评测项目', exact: true })).toBeVisible()
}

export async function chooseProject(page: Page, test: TestId, activate: Activate = click, catalogRevision: CatalogRevision = 3): Promise<void> {
  await activateAvailableSelection(page.getByRole('button', projectSelection(test, catalogRevision)), activate)
  await expect(host(page)).toBeVisible()
}

export async function choosePractice(page: Page, suite: Suite, test: TestId, options: { difficulty?: number; activate?: Activate; catalogRevision?: CatalogRevision } = {}): Promise<void> {
  const activate = options.activate ?? click
  await activateAvailableSelection(page.getByRole('button', { name: `${SUITES[suite].label} 自由练习`, exact: true }), activate)
  await expect(page.getByRole('heading', { name: '自由练习', exact: true })).toBeVisible()
  if (suite !== 'pc-moba') await page.getByRole('combobox', { name: '练习难度', exact: true }).selectOption(String(options.difficulty ?? 3))
  await chooseProject(page, test, activate, options.catalogRevision ?? (suite === 'pc-moba' ? 4 : 3))
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
  await activate(page.getByRole('button', { name: '检查设备并进入试练', exact: true }))
  await expectPhase(page, 'trial', checkpoint)
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
    const singleClick = test === 'gridshot' || test === 'sixshot'
    if (singleClick) {
      await page.mouse.move(box.x + box.width * .5, box.y + box.height * .5)
      await page.mouse.down()
      // A held left button must still contribute exactly one shot in v4.
      await inputGap(page, 350)
      await page.mouse.up()
    }
    for (let index = 0; index < 4; index += 1) {
      await page.mouse.move(box.x + box.width * (0.48 + index * 0.01), box.y + box.height * 0.5, { steps: 3 })
      if (test !== 'tracking' && test !== 'spheretrack90') await page.mouse.click(box.x + box.width * 0.5, box.y + box.height * 0.5)
      await inputGap(page, singleClick ? 75 : 260)
    }
  } else if (test === 'last-hit') {
    await exerciseLaneInput(page)
  } else {
    // Other scenes receive ordinary attack/move and point inputs, not guaranteed hits.
    for (let index = 0; index < 4; index += 1) {
      await page.mouse.click(box.x + box.width * 0.59, box.y + box.height * 0.5, { button: 'right' })
      if (test === 'map-awareness') await page.mouse.click(box.x + box.width * 0.84, box.y + box.height * 0.16)
      await inputGap(page, 1_100)
    }
  }
}

/** Native clicks from the public formation and elapsed clock, never hidden engine state. */
export async function exerciseLaneInput(page: Page): Promise<void> {
  const box = await canvas(page, 'pc-moba').boundingBox()
  if (!box) throw new Error('lane canvas is not visible')
  const elapsed = await host(page).evaluate(element => performance.now() - Number(element.getAttribute('data-run-start-ms')))
  if (!Number.isFinite(elapsed) || elapsed < 0 || elapsed > 3000) throw new Error('native lane input missed the initial formation')
  const point = laneWorldToScreen({ x: 900 - 85 * elapsed / 1000, y: 247.25 })
  await page.mouse.click(box.x + point.x / 1000 * box.width, box.y + point.y / 562.5 * box.height)
  // Let the selected-target chase and repeated actual attacks resolve.
  await inputGap(page, 11000)
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
    async waitSaved(timeout = 90_000): Promise<ResultSaveView> {
      await expect.poll(() => saved !== null, { timeout, message: 'UI result submission must be confirmed by server' }).toBe(true)
      if (!saved || malformed) throw new Error('invalid UI save evidence')
      return saved
    },
    inputResponded(test: TestId): boolean {
      const metrics = saved?.scored.metrics ?? {}
      if (test === 'gridshot' || test === 'sixshot') return metrics.shots === 5
      if (test === 'precision') return typeof metrics.shots === 'number' && metrics.shots >= 3
      if (test === 'last-hit') return (metrics.attacks ?? 0) >= 3
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
export async function captureSafeEvidence(page: Page, testInfo: TestInfo, categories: Record<string, boolean>, phase?: SafeEvidencePhase): Promise<void> {
  const directory = process.env.ESPORTS_E2E_ARTIFACT_DIR
  const caseId = testInfo.title.split(' ')[0]
  if (!directory || !validArtifactIdentity(directory, caseId, categories, phase)) {
    throw new Error('safe assessment artifact identity is unavailable')
  }
  await mkdir(directory, { recursive: true })
  const prefix = `${testInfo.project.name}-${caseId}${phase ? `-${phase}` : ''}`
  // Never capture the account header, report identity, raw snapshots or private URLs.
  const safeRegion = phase?.endsWith('_report') ? page.getByRole('region', { name: '内部训练排名', exact: true }) : page.locator('canvas[aria-label="FPS 测试场景"], canvas[aria-label="MOBA 测评场景"], canvas[aria-label="MOBA 兵线测评场景"]').first()
  if (await safeRegion.isVisible()) await safeRegion.screenshot({ path: join(directory, `${prefix}-scene.png`) })
  if (phase?.startsWith('lane_') && !phase.endsWith('_report')) {
    const hud = page.getByLabel('兵线实时反馈', { exact: true })
    if (await hud.isVisible()) await hud.screenshot({ path: join(directory, `${prefix}-hud.png`) })
  }
  const events = browserEvents.get(page)
  if (!events) throw new Error('browser event observation was not installed')
  // Native observations win over caller categories, including expected recovery faults.
  await writeFile(join(directory, `${prefix}-browser-events.json`), JSON.stringify({ ...categories, ...events }, null, 2))
  const network = networkDiagnostics.get(page)
  if (network) await writeFile(join(directory, `${prefix}-network-events.json`), JSON.stringify(network.snapshot(), null, 2))
}
