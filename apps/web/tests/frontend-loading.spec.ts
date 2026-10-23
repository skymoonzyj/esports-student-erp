import { test, expect } from '@playwright/test'
import { readPrivateJson } from './talentOwnedConfig'

test('served synthesized audio uses trusted gestures and native completion; copied assets remain server-guarded', async ({ page, context, browser }, info) => {
  const payload = info.config.metadata.esportsGames
  if (payload?.mode !== 'audio') throw new Error('Fixed owned audio scenario required')
  const credentials = readPrivateJson(payload.credentialsFile)
  const login = await context.request.post('/api/v1/auth/login', { data: credentials.admin })
  expect(login.status()).toBe(200)
  expect((await login.json()).user.is_super_admin).toBe(true)
  const prefix = '/api/v1/esports-assessments/sound-preview/'
  const files = ['index.html', 'style.css', 'app.mjs', 'core.mjs', 'audio.mjs']
  const observed = new Map<string, { status: number; size: number; mime: string; cache: string }>()
  let mutations = 0
  context.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/v1/esports-assessments/') && request.method() !== 'GET') mutations++
  })
  context.on('response', async response => {
    const pathname = new URL(response.url()).pathname
    if (!files.some(file => pathname === prefix + file)) return
    const body = await response.body()
    observed.set(pathname.slice(prefix.length), { status: response.status(), size: body.length,
      mime: response.headers()['content-type'] ?? '', cache: response.headers()['cache-control'] ?? '' })
  })
  await context.addInitScript(() => {
    const gestures: Array<{ trusted: boolean; active: boolean }> = []
    Object.assign(window, { __audioGestures: gestures })
    document.addEventListener('click', event => {
      if (event.target instanceof Element && ['left', 'right', 'play'].includes(event.target.id)) {
        gestures.push({ trusted: event.isTrusted, active: navigator.userActivation.isActive })
      }
    }, { capture: true, passive: true })
  })
  await page.goto('/esports-assessments')
  const popupEvent = context.waitForEvent('page')
  await page.getByRole('link', { name: '听声辨位（试听）', exact: true }).click()
  const popup = await popupEvent
  await popup.bringToFront()
  await expect(popup.getByRole('heading', { name: '听声辨位', exact: true })).toBeVisible()
  await expect.poll(() => observed.size).toBe(5)
  for (const [file, response] of observed) {
    expect(response.status).toBe(200)
    expect(response.size).toBeGreaterThan(0)
    expect(response.cache).toContain('no-store')
    expect(response.cache).toContain('private')
    expect(response.mime).toContain(file.endsWith('.mjs') ? 'javascript' : file.endsWith('.css') ? 'text/css' : 'text/html')
  }
  await expect(popup.locator('#start')).toBeDisabled()
  await popup.locator('#device').selectOption('wired')
  for (const [id, label] of [['left', '左耳试听完成'], ['right', '右耳试听完成']]) {
    await popup.locator('#' + id).click()
    await expect(popup.locator('#stop-calibration')).toBeEnabled()
    await expect(popup.locator('#audio-status')).toHaveText(label)
  }
  // Synthetic workflow confirmation only, never a claim about actual audible output.
  await popup.locator('#confirmed').check()
  await popup.locator('#start').click()
  await popup.locator('#play').click()
  await expect(popup.locator('#stop')).toBeEnabled()
  await expect(popup.locator('#trial-status')).toHaveText('声音播放完成，请选择你听到的方向。')
  await expect(popup.locator('.direction').first()).toBeEnabled()
  const gestures = await popup.evaluate(() => (window as typeof window & { __audioGestures: Array<{ trusted: boolean; active: boolean }> }).__audioGestures)
  expect(gestures).toEqual(Array.from({ length: 3 }, () => ({ trusted: true, active: true })))
  expect(mutations).toBe(0)
  await popup.close()
  const restricted = await browser.newContext({ baseURL: payload.baseURL, serviceWorkers: 'block' })
  try {
    const deniedLogin = await restricted.request.post('/api/v1/auth/login', { data: credentials.customer_service })
    expect(deniedLogin.status()).toBe(200)
    expect((await deniedLogin.json()).user.is_super_admin).toBe(false)
    const deniedPage = await restricted.newPage()
    await deniedPage.goto('/')
    await expect(deniedPage.getByRole('link', { name: '听声辨位（试听）', exact: true })).toHaveCount(0)
    for (const file of files) {
      const response = await restricted.request.get(prefix + file)
      expect(response.status()).toBe(403)
      expect((await response.json()).code).toBe('esports_forbidden')
    }
  } finally { await restricted.close() }
  info.annotations.push({ type: 'loading-evidence', description: JSON.stringify({ resources200: observed.size,
    trustedActiveGestures: gestures.length, nativeCompletions: 3, generatedPcmNotFetchedAudio: true,
    apiMutations: mutations, forbiddenAssets: files.length, audibleOutputNotVerified: true, physicalDeviceNotVerified: true }) })
})
