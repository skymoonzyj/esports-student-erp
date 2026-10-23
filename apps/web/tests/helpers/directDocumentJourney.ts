import { readFileSync } from 'node:fs'
import { expect as playwrightExpect, type Browser, type Page, type TestInfo } from '@playwright/test'
import { loadingProfiles } from '../../playwright.loading.config'
import { classifyReadyResource, installReadyProbe } from './readyChainDiagnostic.mjs'
import { assembleDirectPhase, captureDirect, directFailure, directPlan, installDirectWitnesses, observeDirectNetwork } from './directDocumentDiagnostic.mjs'

const expect = playwrightExpect.configure({ timeout: 60000 })
type ProbeWindow = Window & {
  __readyChain?: { mark: (name: string) => void; snapshot: () => Record<string, unknown>; dispose: () => void }
  __directWitnesses?: { snapshot: () => unknown; dispose: () => void }
}
const mark = (page: Page, name: string) => page.evaluate(name => (window as ProbeWindow).__readyChain?.mark(name), name)

// Exact original assertions/order, with passive marks beside them; no ready
// replacement or application hooks. In particular mobile opens the real filter.
async function ready(page: Page, scenario: string, setStage: (stage: string) => void) {
  if (scenario === 'housing') {
    setStage('heading')
    await expect(page.getByRole('heading', { name: '住宿房态', exact: true })).toBeVisible(); await mark(page, 'heading')
    setStage('room_card'); await expect(page.locator('.housingRoomCard').first()).toBeVisible(); await mark(page, 'room_card')
    if (page.viewportSize()!.width < 720) {
      setStage('filters_open'); await page.getByRole('button', { name: '打开筛选', exact: true }).click(); await mark(page, 'filters_open')
    }
    setStage('filter_search'); await expect(page.locator('.housingFilters').getByLabel('搜索房间', { exact: true })).toBeEditable(); await mark(page, 'filter_search')
    setStage('filter_apply'); await expect(page.locator('.housingFilters').getByRole('button', { name: '应用筛选', exact: true })).toBeEnabled(); await mark(page, 'filter_apply')
  } else {
    setStage('heading')
    await expect(page.getByRole('heading', { name: '人员中心', exact: true })).toBeVisible(); await mark(page, 'heading')
    setStage('table'); await expect(page.getByRole('table', { name: '人员名单', exact: true })).toBeVisible(); await mark(page, 'table')
    setStage('person_search'); await expect(page.getByLabel('姓名或昵称', { exact: true })).toBeEditable(); await mark(page, 'person_search')
    setStage('person_query'); await expect(page.getByRole('button', { name: '查询', exact: true })).toBeEnabled(); await mark(page, 'person_query')
    setStage('updating_absent'); await expect(page.getByText('正在更新…', { exact: true })).toHaveCount(0); await mark(page, 'updating_absent')
  }
  setStage('ready_mark'); await mark(page, 'ready')
}

export async function observeDirectDocuments(browser: Browser, info: TestInfo) {
  const payload = info.config.metadata.directDocument
  const phases: unknown[] = [], plan = directPlan()
  const failures: ReturnType<typeof directFailure>[] = []
  const rejections: { schemaVersion: number; phaseIndex: number; validator: string; field: string; rule: string; itemIndex: number | null }[] = []
  let stage = 'context', phaseIndex = 0
  const setStage = (value: string) => { stage = value }
  const record = (kind: string, error?: unknown) => {
    const point = kind === 'validation_rejected' ? 'validation' : kind === 'evidence_incomplete' ? 'completeness'
      : kind === 'sink_exception' ? 'phase_save' : stage
    if (failures.length < 8) failures.push(directFailure(kind, point, phaseIndex, error))
    else failures[7] = directFailure('evidence_incomplete', 'failure_overflow', phaseIndex)
  }
  try {
    for (const cell of plan.filter(item => item.phase === 'cold')) {
      const viewport = cell.viewport === 'desktop' ? { width: 1440, height: 900 } : { width: 390, height: 844 }
      stage = 'context'; phaseIndex = phases.length
      const context = await browser.newContext({ baseURL: payload.baseURL, viewport, serviceWorkers: 'block' })
      let contextFailed = false
      try {
        stage = 'init'
        await context.addInitScript({ content: `(${installReadyProbe.toString()})(${JSON.stringify(cell.scenario)}, ${classifyReadyResource.toString()});(${installDirectWitnesses.toString()})()` })
        stage = 'auth'
        const auth = await context.request.post('/api/v1/auth/login', { data: JSON.parse(readFileSync(payload.credentialsFile, 'utf8')) })
        expect(auth.status()).toBe(200)
        stage = 'page'; const page = await context.newPage()
        page.setDefaultTimeout(60000)
        stage = 'cdp'; const cdp = await context.newCDPSession(page)
        let failed = false
        const network = observeDirectNetwork(cdp, new URL(payload.baseURL).origin, (await cdp.send('Page.getFrameTree')).frameTree.frame.id)
        try {
          await cdp.send('Network.enable')
          await cdp.send('Performance.enable')
          await cdp.send('Network.emulateNetworkConditions', loadingProfiles[cell.profile as keyof typeof loadingProfiles])
          await cdp.send('Emulation.setCPUThrottlingRate', { rate: cell.profile === 'throttled' ? 4 : 1 })
          await cdp.send('Network.setCacheDisabled', { cacheDisabled: true })
          for (const phase of ['cold', 'prime', 'warm']) {
            phaseIndex = phases.length; stage = 'cache'
            if (phase === 'prime') await cdp.send('Network.setCacheDisabled', { cacheDisabled: false })
            network.begin()
            await captureDirect(async () => {
              stage = 'navigate'
              if (phase === 'prime') await page.reload({ waitUntil: 'domcontentloaded' })
              else await page.goto(cell.scenario === 'housing' ? '/housing' : '/people', { waitUntil: 'domcontentloaded', timeout: 60000 })
              stage = 'goto_mark'; await mark(page, 'goto_done')
              await ready(page, cell.scenario, setStage)
              stage = 'settle'; await page.waitForTimeout(2000); await mark(page, 'settled')
            }, async (actionFailed: boolean) => {
              stage = 'snapshot'
              const observed = await page.evaluate(() => ({ snapshot: (window as ProbeWindow).__readyChain?.snapshot(),
                witnesses: (window as ProbeWindow).__directWitnesses?.snapshot(), timeOrigin: performance.timeOrigin })).catch(error => { record('observer_exception', error); return undefined })
              stage = 'metrics'
              const value = await cdp.send('Performance.getMetrics').then(result => result.metrics.find(m => m.name === 'ScriptDuration')?.value).catch(error => { record('observer_exception', error); return undefined })
              stage = 'network_snapshot'; const evidence = network.snapshot(observed?.timeOrigin ?? null)
              return assembleDirectPhase(cell, phase, observed, value, evidence, actionFailed)
            }, (value: unknown) => { phases.push(value) }, payload.fingerprint?.sourceCommit, record, reason => {
              if (!rejections.length) rejections.push({ schemaVersion: 1, phaseIndex, validator: reason.validator,
                field: reason.field, rule: reason.rule, itemIndex: reason.itemIndex })
            })
          }
        } catch (error) { failed = true; throw error }
        finally {
          if (!failed) stage = 'cleanup'
          network.dispose()
          await page.evaluate(() => { (window as ProbeWindow).__readyChain?.dispose(); (window as ProbeWindow).__directWitnesses?.dispose() }).catch(() => undefined)
          await cdp.detach().catch(error => { if (!failed) throw error })
        }
      } catch (error) { contextFailed = true; throw error }
      finally { if (!contextFailed) stage = 'cleanup'; await context.close().catch(error => { if (!contextFailed) throw error }) }
    }
  } catch (error) { if (!failures.length) record('action_exception', error); throw error }
  finally {
    info.annotations.push({ type: 'direct-document-evidence', description: JSON.stringify(phases) })
    info.annotations.push({ type: 'direct-document-failures', description: JSON.stringify(failures) })
    info.annotations.push({ type: 'direct-document-rejections', description: JSON.stringify(rejections) })
  }
}
