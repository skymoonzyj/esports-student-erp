import { createHash } from 'node:crypto'
import { existsSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { test, expect, type BrowserContext, type Page, type Route } from '@playwright/test'

type Asset = { path: string; sha256: string; size: number }
type Release = { releaseId: string; assets: { js: Asset; css: Asset } }
const hash = (body: Buffer) => createHash('sha256').update(body).digest('hex')
const reasonField = (page: Page) => page.getByRole('region', { name: '协助档案资料更正', exact: true }).locator('textarea')

test('real A tabs survive B and C; expired D CSS failure preserves an actual sibling correction draft', async ({ browser }, info) => {
  const payload = info.config.metadata.releaseWindow
  if (!payload) throw new Error('Owned release runner required')
  const a = payload.releases.A as Release
  const stage = (value: string) => writeFileSync(path.join(payload.barrierDir, 'browser-stage.json'), JSON.stringify({ stage: value }), { mode: 0o600 })
  const contexts: BrowserContext[] = []
  const cases: Array<{ name: string; success: Page; draft: Page; held: () => Route | undefined;
    reasonNode: Awaited<ReturnType<ReturnType<Page['getByLabel']>['elementHandle']>>;
    counts: { documents: number; logouts: number; corrections: number }; responses: Map<string, { status: number; sha: string; size: number; cache: string; mime: string }> }> = []
  async function barrier(stage: 'ready-A' | 'ready-D', next: 'active-C' | 'active-D') {
    const request = path.join(payload.barrierDir, stage + '.json')
    writeFileSync(request + '.tmp', JSON.stringify({ stage }), { mode: 0o600 })
    renameSync(request + '.tmp', request)
    const response = path.join(payload.barrierDir, next + '.json')
    await expect.poll(() => existsSync(response), { timeout: 600000, intervals: [200] }).toBe(true)
    return JSON.parse(readFileSync(response, 'utf8')).releases as Record<string, Release>
  }
  async function newPage(viewport: { width: number; height: number }, touch: boolean) {
    const context = await browser.newContext({ viewport, hasTouch: touch, isMobile: touch,
      baseURL: payload.baseURL, serviceWorkers: 'block' })
    contexts.push(context)
    const response = await context.request.post('/api/v1/auth/login', { data: JSON.parse(readFileSync(payload.credentialsFile, 'utf8')) })
    expect(response.status()).toBe(200)
    return context.newPage()
  }
  try {
    for (const [name, width, height, touch] of [['desktop', 1440, 900, false], ['touch', 390, 844, true]] as const) {
      stage(name + '-success-home')
      const success = await newPage({ width, height }, touch)
      const responses = new Map<string, { status: number; sha: string; size: number; cache: string; mime: string }>()
      success.on('response', async response => {
        const key = new URL(response.url()).pathname.slice(1)
        if (![a.assets.js.path, a.assets.css.path].includes(key)) return
        const body = await response.body()
        responses.set(key, { status: response.status(), sha: hash(body), size: body.length,
          cache: response.headers()['cache-control'] ?? '', mime: response.headers()['content-type'] ?? '' })
      })
      await success.goto('/esports-assessments')
      await expect(success.getByRole('link', { name: '查看评测记录', exact: true })).toBeVisible()
      expect(responses.size).toBe(0)
      expect(await success.evaluate(paths => performance.getEntriesByType('resource').some(item => paths.some(path => item.name.endsWith('/' + path))), [a.assets.js.path, a.assets.css.path])).toBe(false)
      const draft = await newPage({ width, height }, touch)
      const lifecycle = { documents: 0, authFailures: 0 }
      draft.on('request', request => { if (request.resourceType() === 'document') lifecycle.documents++ })
      draft.on('response', response => { if ([401, 403].includes(response.status())) lifecycle.authFailures++ })
      stage(name + '-draft-person')
      let held: Route | undefined
      await draft.route(payload.baseURL + '/' + a.assets.css.path, route => { held = route })
      const people = await draft.context().request.get('/api/v1/people', { params: { query: 'E2E协助种子1', page_size: 100 } })
      expect(people.status()).toBe(200)
      const person = (await people.json()).items.find((item: { full_name: string }) => item.full_name === 'E2E协助种子1')
      expect(person).toBeTruthy()
      await draft.goto('/people/' + person.id, { waitUntil: 'domcontentloaded' })
      stage(name + '-draft-css-held')
      await expect.poll(() => Boolean(held)).toBe(true)
      await draft.getByRole('button', { name: '更正本人及监护资料', exact: true }).click()
      stage(name + '-draft-fields')
      // React synchronizes textarea.defaultValue; Playwright's implicit-label
      // exact text then includes that text node. Retain identity before typing.
      const reasonNode = await draft.getByLabel('资料更正原因', { exact: true }).elementHandle()
      await draft.getByLabel('资料更正原因', { exact: true }).fill('Synthetic retained correction draft')
      await draft.getByRole('region', { name: '本人资料', exact: true }).getByLabel('姓名', { exact: true }).fill('Synthetic retained name')
      const reason = reasonField(draft)
      await expect(reason).toHaveCount(1)
      expect(await reason.evaluate((node, original) => node === original && node.isConnected, reasonNode)).toBe(true)
      const nodes = await reason.evaluateAll(elements => ({ count: elements.length, connected: elements.length === 1 && elements[0].isConnected }))
      info.annotations.push({ type: 'release-evidence', description: JSON.stringify({ viewport: name,
        diagnostic: 'before-reason-handle', ...nodes, ...lifecycle }) })
      const counts = { documents: 0, logouts: 0, corrections: 0 }
      for (const page of [success, draft]) page.on('request', request => {
        const url = new URL(request.url()).pathname
        if (request.resourceType() === 'document') counts.documents++
        if (url === '/api/v1/auth/logout') counts.logouts++
        if (url.endsWith('/corrections') && request.method() === 'POST') counts.corrections++
      })
      cases.push({ name, success, draft, held: () => held, reasonNode, counts, responses })
    }
    const c = await barrier('ready-A', 'active-C')
    stage('late-history-after-C')
    for (const item of cases) {
      await item.success.getByRole('link', { name: '查看评测记录', exact: true }).click()
      await expect(item.success.getByRole('heading', { name: '评测记录', exact: true })).toBeVisible()
      await expect(item.success.locator('[data-release-window="A"]')).toBeVisible()
      expect(await item.success.locator('[data-release-window="A"]').evaluate(element => getComputedStyle(element).getPropertyValue('--erp-release-fixture').trim())).toBe('A')
      await expect.poll(() => item.responses.size).toBe(2)
      for (const kind of ['js', 'css'] as const) {
        const response = item.responses.get(a.assets[kind].path)!
        expect(response).toMatchObject({ status: 200, sha: a.assets[kind].sha256, size: a.assets[kind].size })
        expect(response.cache).toContain('immutable')
        expect(response.mime).toContain(kind === 'js' ? 'javascript' : 'text/css')
        expect(response.sha).not.toBe(c.B.assets[kind].sha256)
        expect(response.sha).not.toBe(c.C.assets[kind].sha256)
      }
      expect(item.counts.documents).toBe(0)
    }
    const d = await barrier('ready-D', 'active-D')
    for (const item of cases) {
      for (const asset of Object.values(a.assets)) {
        const response = await item.draft.context().request.get('/' + asset.path)
        expect(response.status()).toBe(404)
        expect(response.headers()['cache-control'] ?? '').not.toContain('immutable')
        expect((await response.text()).toLowerCase()).not.toContain('<div id="root"')
      }
      const css = item.draft.waitForResponse(response => new URL(response.url()).pathname === '/' + a.assets.css.path)
      await item.held()!.continue()
      expect((await css).status()).toBe(404)
      await expect(item.draft.getByText('系统已更新', { exact: true })).toBeVisible()
      expect(await item.reasonNode!.evaluate(node => node.isConnected)).toBe(true)
      await expect(reasonField(item.draft)).toHaveCount(1)
      expect(await reasonField(item.draft).evaluate((node, original) => node === original, item.reasonNode)).toBe(true)
      await expect(reasonField(item.draft)).toHaveValue('Synthetic retained correction draft')
      item.draft.once('dialog', dialog => dialog.dismiss())
      await item.draft.getByRole('button', { name: '刷新页面', exact: true }).click()
      expect(await item.reasonNode!.evaluate(node => node.isConnected)).toBe(true)
      await expect(item.draft.getByRole('region', { name: '本人资料', exact: true }).getByLabel('姓名', { exact: true })).toHaveValue('Synthetic retained name')
      await expect(reasonField(item.draft)).toHaveCount(1)
      expect(await reasonField(item.draft).evaluate((node, original) => node === original, item.reasonNode)).toBe(true)
      await reasonField(item.draft).fill('Still editable after cancellation')
      await expect(reasonField(item.draft)).toHaveValue('Still editable after cancellation')
      expect(item.counts).toEqual({ documents: 0, logouts: 0, corrections: 0 })
      item.draft.once('dialog', dialog => dialog.accept())
      await item.draft.getByRole('button', { name: '刷新页面', exact: true }).click()
      await expect.poll(() => item.counts.documents).toBe(1)
      await expect(item.draft.getByRole('button', { name: '更正本人及监护资料', exact: true })).toBeVisible()
      expect((await (await item.draft.context().request.get('/build-info.json')).json()).build_id).toBe(d.D.releaseId)
      info.annotations.push({ type: 'release-evidence', description: JSON.stringify({ viewport: item.name,
        unmodifiedSuccessNetwork: true, oldJsAndCssHttp200HashMatched: true, oldJsAndCssReal404: true,
        draftFailure: 'held-real-CSS-only', sameDraftNodeAfterCancel: true, noAutomaticNavigationOrLogout: true,
        corrections: item.counts.corrections, explicitConfirmedNavigation: item.counts.documents, releases: d }) })
    }
  } finally { await Promise.all(contexts.map(context => context.close())) }
})
