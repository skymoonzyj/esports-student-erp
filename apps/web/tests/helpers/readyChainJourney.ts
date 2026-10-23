import { readFileSync } from 'node:fs'
import { expect as playwrightExpect, type Browser, type Page, type TestInfo } from '@playwright/test'
import { loadingProfiles } from '../../playwright.loading.config'
import { classifyReadyResource, installReadyProbe, readyPhaseComplete, validateReadyPhase } from './readyChainDiagnostic.mjs'

const expect = playwrightExpect.configure({ timeout: 60000 })
type Scenario = 'login' | 'housing' | 'personnel'
type Probe = { begin: (spa: boolean) => void; mark: (name: string) => void; snapshot: () => Record<string, unknown>; dispose: () => void }
type ProbeWindow = Window & { __readyChain?: Probe }
const mark = (page: Page, name: string) => page.evaluate(name => (window as ProbeWindow).__readyChain?.mark(name), name)

// Same predicates/order as measure-loading.mjs; timestamps are observations only.
async function ready(page: Page, scenario: Scenario) {
  if (scenario === 'login') {
    await expect(page.getByRole('heading', { name: '登录工作台', exact: true })).toBeVisible(); await mark(page, 'heading')
    await expect(page.getByLabel('手机号或邮箱', { exact: true })).toBeEditable(); await mark(page, 'identifier')
    await expect(page.getByLabel('密码', { exact: true })).toBeEditable(); await mark(page, 'password')
    await expect(page.getByRole('button', { name: '登录', exact: true })).toBeEnabled(); await mark(page, 'submit')
  } else if (scenario === 'housing') {
    await expect(page.getByRole('heading', { name: '住宿房态', exact: true })).toBeVisible(); await mark(page, 'heading')
    await expect(page.locator('.housingRoomCard').first()).toBeVisible(); await mark(page, 'room_card')
    if (page.viewportSize()!.width < 720) {
      await page.getByRole('button', { name: '打开筛选', exact: true }).click(); await mark(page, 'filters_open')
    }
    await expect(page.locator('.housingFilters').getByLabel('搜索房间', { exact: true })).toBeEditable(); await mark(page, 'filter_search')
    await expect(page.locator('.housingFilters').getByRole('button', { name: '应用筛选', exact: true })).toBeEnabled(); await mark(page, 'filter_apply')
  } else {
    await expect(page.getByRole('heading', { name: '人员中心', exact: true })).toBeVisible(); await mark(page, 'heading')
    await expect(page.getByRole('table', { name: '人员名单', exact: true })).toBeVisible(); await mark(page, 'table')
    await expect(page.getByLabel('姓名或昵称', { exact: true })).toBeEditable(); await mark(page, 'person_search')
    await expect(page.getByRole('button', { name: '查询', exact: true })).toBeEnabled(); await mark(page, 'person_query')
    await expect(page.getByText('正在更新…', { exact: true })).toHaveCount(0); await mark(page, 'updating_absent')
  }
  await mark(page, 'ready')
}

// Genuine links and mobile sheet actions, unchanged from the fixed collector.
async function navigate(page: Page, destination: string) {
  const closeFilter = page.getByRole('button', { name: '关闭筛选', exact: true })
  if (await closeFilter.isVisible()) await closeFilter.click()
  await mark(page, 'nav_filter_checked')
  const visibleLink = page.getByRole('link', { name: destination, exact: true }).filter({ visible: true }).first()
  if (!await visibleLink.isVisible() && page.viewportSize()!.width < 720) {
    await page.getByRole('button', { name: '更多', exact: true }).click()
  }
  await mark(page, 'nav_menu_checked')
  await page.getByRole('link', { name: destination, exact: true }).filter({ visible: true }).first().click()
  await mark(page, 'nav_clicked')
  const closeMore = page.getByRole('button', { name: '关闭更多导航', exact: true })
  if (await closeMore.isVisible()) await closeMore.click()
  await mark(page, 'nav_close_checked')
}

export async function observeReadyChains(browser: Browser, viewport: { width: number; height: number }, info: TestInfo) {
  const payload = info.config.metadata.routeLoading
  const phases: unknown[] = []
  try {
    // Preserve the original eleven phases, then observe each business page in a
    // separate fresh context under the collector's existing throttled profile.
    for (const [scenario, profile] of [['login', 'throttled'], ['housing', 'unthrottled'], ['personnel', 'unthrottled'],
      ['housing', 'throttled'], ['personnel', 'throttled']] as const) {
      const context = await browser.newContext({ baseURL: payload.baseURL, viewport, serviceWorkers: 'block' })
      try {
        await context.addInitScript({ content: `(${installReadyProbe.toString()})(${JSON.stringify(scenario)}, ${classifyReadyResource.toString()})` })
        if (scenario !== 'login') {
          const response = await context.request.post('/api/v1/auth/login', { data: JSON.parse(readFileSync(payload.credentialsFile, 'utf8')) })
          expect(response.status()).toBe(200)
        }
        const page = await context.newPage()
        page.setDefaultTimeout(60000)
        const cdp = await context.newCDPSession(page)
        await cdp.send('Network.enable')
        await cdp.send('Performance.enable')
        await cdp.send('Network.emulateNetworkConditions', loadingProfiles[profile])
        await cdp.send('Emulation.setCPUThrottlingRate', { rate: profile === 'throttled' ? 4 : 1 })
        // Request IDs/headers stay in this bounded private closure, never annotations.
        const frameId = (await cdp.send('Page.getFrameTree')).frameTree.frame.id
        let documentId: string | null = null
        let document = { status: null as number | null, extraInfoStatus: null as number | null, noCache: null as boolean | null }
        cdp.on('Network.requestWillBeSent', event => {
          if (event.type !== 'Document' || event.frameId !== frameId) return
          documentId = event.requestId
          document = { status: null, extraInfoStatus: null, noCache: null }
        })
        cdp.on('Network.responseReceived', event => {
          if (event.requestId !== documentId) return
          document.status = event.response.status
          const cache = Object.entries(event.response.headers).find(([key]) => key.toLowerCase() === 'cache-control')?.[1]
          document.noCache = typeof cache === 'string' ? /(?:^|,)\s*no-cache(?:\s*(?:,|$))/.test(cache) : null
        })
        cdp.on('Network.responseReceivedExtraInfo', event => {
          if (event.requestId === documentId) document.extraInfoStatus = event.statusCode
        })
        const capture = async (phase: string, action: () => Promise<void>, spa = false) => {
          const scriptDuration = async () => {
            const value = await cdp.send('Performance.getMetrics')
              .then(result => result.metrics.find(metric => metric.name === 'ScriptDuration')?.value).catch(() => undefined)
            return value !== undefined && Number.isFinite(value) && value >= 0 && value <= 90 ? value : undefined
          }
          const beforeScript = spa ? await scriptDuration() : 0
          if (spa) await page.evaluate(() => (window as ProbeWindow).__readyChain?.begin(true))
          let failed = true
          try { await action(); failed = false }
          finally {
            const snapshot = await page.evaluate(() => (window as ProbeWindow).__readyChain?.snapshot()).catch(() => undefined)
            const afterScript = await scriptDuration()
            const { earlier, ...current } = snapshot ?? {}
            const expectedEarlier = spa ? ['setup', 'first', 'back', 'again'].indexOf(phase) : 0
            let accountingAvailable = Array.isArray(earlier) && earlier.length === expectedEarlier && expectedEarlier >= 0 && expectedEarlier <= 3
            if (accountingAvailable && Array.isArray(earlier)) earlier.forEach((accounting, index) => {
              const priorIndex = phases.length - earlier.length + index
              const prior = validateReadyPhase(phases[priorIndex])
              if (!prior || prior.scenario !== scenario || prior.profile !== profile || prior.phase !== ['setup', 'first', 'back'][index]
                || accounting === null || typeof accounting !== 'object' || Array.isArray(accounting)
                || Object.keys(accounting).sort().join('|') !== 'cost|journeyCost|overflow|resources') {
                accountingAvailable = false; return
              }
              // Backfill started-in-phase resources only. Original readiness,
              // DOM, FCP and captured execution durations remain immutable.
              const refreshed = validateReadyPhase({ ...prior, resources: accounting.resources,
                overflow: prior.overflow || accounting.overflow,
                cost: { ...accounting.cost, scriptDurationMs: prior.cost.scriptDurationMs },
                journeyCost: { ...accounting.journeyCost, scriptDurationMs: prior.journeyCost.scriptDurationMs } })
              phases[priorIndex] = refreshed
              if (!readyPhaseComplete(refreshed)) accountingAvailable = false
            })
            const phaseScriptDuration = beforeScript !== undefined && afterScript !== undefined && afterScript >= beforeScript
              ? (afterScript - beforeScript) * 1000 : null
            const result = validateReadyPhase({ scenario, phase, profile,
              ...(snapshot ? current : { resources: [], stages: [], dom: [], overflow: false, fcpMs: null }),
              cost: { ...(snapshot?.cost as object), scriptDurationMs: phaseScriptDuration },
              journeyCost: { ...(snapshot?.journeyCost as object), scriptDurationMs: afterScript !== undefined ? afterScript * 1000 : null },
              document: spa ? { status: null, extraInfoStatus: null, noCache: null } : document,
              unavailable: !snapshot || failed || phaseScriptDuration === null || !accountingAvailable })
            phases.push(result)
            // Preserve the original assertion when action failed; do not replace it.
            if (!failed) expect(readyPhaseComplete(result), 'fixed ready-chain phase must be complete and bounded').toBe(true)
          }
        }
        const settle = async () => { await page.waitForTimeout(2000); await mark(page, 'settled') }
        const workbench = async () => {
          await expect(page.getByRole('heading', { name: '工作台', exact: true })).toBeVisible(); await mark(page, 'workbench')
        }
        try {
          if (scenario === 'login') {
            await cdp.send('Network.setCacheDisabled', { cacheDisabled: true })
            await capture('cold', async () => {
              await page.goto('/login', { waitUntil: 'domcontentloaded', timeout: 60000 }); await mark(page, 'goto_done')
              await ready(page, 'login'); await settle()
            })
            await cdp.send('Network.setCacheDisabled', { cacheDisabled: false })
            await capture('prime', async () => {
              await page.reload({ waitUntil: 'domcontentloaded' }); await mark(page, 'goto_done')
              await ready(page, 'login'); await settle()
            })
            await capture('warm', async () => {
              await page.goto('/login', { waitUntil: 'domcontentloaded', timeout: 60000 }); await mark(page, 'goto_done')
              await ready(page, 'login'); await settle()
            })
          } else {
            await capture('setup', async () => {
              await page.goto('/', { waitUntil: 'domcontentloaded' }); await mark(page, 'goto_done')
              await workbench(); await settle()
            })
            await capture('first', async () => {
              await navigate(page, scenario === 'housing' ? '房态管理' : '人员中心')
              await ready(page, scenario); await settle()
            }, true)
            await capture('back', async () => { await navigate(page, '工作台'); await workbench() }, true)
            await capture('again', async () => {
              await navigate(page, scenario === 'housing' ? '房态管理' : '人员中心')
              await ready(page, scenario); await settle()
            }, true)
          }
        } finally {
          await page.evaluate(() => (window as ProbeWindow).__readyChain?.dispose()).catch(() => undefined)
          await cdp.detach()
        }
      } finally { await context.close() }
    }
  } finally {
    info.annotations.push({ type: 'ready-chain-evidence', description: JSON.stringify(phases) })
  }
}
