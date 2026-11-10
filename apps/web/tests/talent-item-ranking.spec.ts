import { randomUUID } from 'node:crypto'
import { mkdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'

import { expect, test, type APIRequestContext } from '@playwright/test'

import { E2E_CREDENTIALS } from './e2eCredentials'
import { createRequestSession, loginWithIdentifier, logout } from './helpers'

const PREFIX = '/api/v1/talent-tests'
type Session = { id: string; results: Array<{ id: string; score: number }> }
type Actor = { api: APIRequestContext; csrfToken: string }

async function start(actor: Actor): Promise<Session> {
  const context = await actor.api.get(`${PREFIX}/context`)
  expect(context.status()).toBe(200)
  const { catalog } = await context.json()
  const response = await actor.api.post(`${PREFIX}/sessions`, {
    headers: { 'X-CSRF-Token': actor.csrfToken },
    data: { request_id: randomUUID(), catalog_version: catalog.catalog_version },
  })
  expect(response.status()).toBe(201)
  return response.json()
}

async function save(actor: Actor, session: Session, milliseconds: number) {
  const data = {
    test_id: 'reactiontime', engine_version: 'reactiontime-clone-v1', locale: 'zh-CN',
    submission_id: randomUUID(), raw: { samples_ms: Array(5).fill(milliseconds), too_soon_count: 0 },
    device: { input_kind: 'mouse', viewport_width: 1280, viewport_height: 720, geometry: null },
    duration_ms: milliseconds * 5, background_interrupted: false,
  }
  const options = { headers: { 'X-CSRF-Token': actor.csrfToken }, data }
  const endpoint = `${PREFIX}/sessions/${session.id}/results`
  const response = await actor.api.post(endpoint, options)
  expect(response.status()).toBe(201)
  const { result } = await response.json()
  const replay = await actor.api.post(endpoint, options)
  expect(replay.status()).toBe(200)
  expect((await replay.json()).result.id).toBe(result.id)
  const persisted = await actor.api.get(`${PREFIX}/sessions/${session.id}`)
  expect(persisted.status()).toBe(200)
  expect((await persisted.json()).results.map((row: { id: string }) => row.id)).toEqual([result.id])
  return result as { id: string; score: number }
}

test.use({ trace: 'off', screenshot: 'off', video: 'off' })

test.afterEach(async ({ browserName }, info) => {
  const directory = process.env.TALENT_E2E_ARTIFACT_DIR
  if (!directory) return
  const failureLines = info.errors.flatMap(error => Array.from(
    error.stack?.matchAll(/talent-item-ranking\.spec\.ts:(\d+):\d+/g) ?? [],
    match => Number(match[1]),
  ))
  await mkdir(directory, { recursive: true })
  await writeFile(join(directory, `${info.project.name}-item-ranking-safe-location.json`), JSON.stringify({
    status: info.status, expectedStatus: info.expectedStatus, browserName,
    negativeControl: process.env.TALENT_ITEM_RANKING_E2E_RED === '1',
    failureLines: [...new Set(failureLines)],
  }))
})

test('单项真实保存、重复测试、匿名前三差距和退出保护', async ({ page }) => {
  test.setTimeout(90_000)
  const login = page.waitForResponse(response => response.url().endsWith('/api/v1/auth/login'))
  await loginWithIdentifier(page, E2E_CREDENTIALS.student.identifier, E2E_CREDENTIALS.student.password)
  const main: Actor = { api: page.request, csrfToken: (await (await login).json()).csrf_token }
  const other = await createRequestSession(
    E2E_CREDENTIALS.ready_male_student.identifier, E2E_CREDENTIALS.ready_male_student.password,
  )
  try {
    const firstSession = await start(main)
    const first = await save(main, firstSession, 200)
    const otherSession = await start(other)
    const competitor = await save(other, otherSession, 100)
    expect(competitor.id).not.toBe(first.id)
    expect(competitor.score).toBeGreaterThan(first.score)
    const abandon = await main.api.post(`${PREFIX}/sessions/${firstSession.id}/abandon`, {
      headers: { 'X-CSRF-Token': main.csrfToken }, data: { request_id: randomUUID() },
    })
    expect(abandon.status()).toBe(200)
    const repeatSession = await start(main)
    const repeat = await save(main, repeatSession, 400)
    expect(new Set([first.id, competitor.id, repeat.id]).size).toBe(3)
    expect(repeat.score).toBeLessThan(first.score)
    const endpoint = `${PREFIX}/results/${repeat.id}/ranking`
    for (let read = 0; read < 2; read += 1) {
      const response = await main.api.get(endpoint)
      expect(response.status()).toBe(200)
      expect(response.headers()['cache-control']).toBe('no-store, private')
      const value = await response.json()
      expect(value.participant_count).toBe(2)
      expect(value.current).toEqual({ rank: 2, values: { score: repeat.score } })
      expect(value.best).toEqual({ rank: 2, values: { score: first.score } })
      expect(value.top_three).toHaveLength(2)
      expect(value.top_three.map((row: { values: { score: number } }) => row.values.score))
        .toEqual([competitor.score, first.score])
      expect(value.top_three[0].gaps).toEqual([
        { key: 'score', amount: competitor.score - repeat.score, state: 'behind' },
      ])
      expect(value.top_three.map((row: { is_self: boolean }) => row.is_self)).toEqual([false, true])
      for (const row of value.top_three) {
        expect(Object.keys(row).sort()).toEqual(['gaps', 'is_self', 'rank', 'tie_count', 'values'])
      }
    }
    // Optional negative control only: real saves/API assertions above still run.
    // A failing run proves the UI assertions notice an unavailable ranking read;
    // it is fault injection, never normal acceptance or preimplementation RED.
    if (process.env.TALENT_ITEM_RANKING_E2E_RED === '1') {
      await page.route('**/api/v1/talent-tests/results/*/ranking', route => route.fulfill({
        status: 404, contentType: 'application/problem+json',
        headers: { 'Cache-Control': 'no-store, private' },
        body: JSON.stringify({ code: 'talent_not_found', detail: 'Synthetic negative control' }),
      }))
    }
    await page.goto(`/talent-tests/sessions/${repeatSession.id}/reactiontime`)
    const panel = page.getByRole('region', { name: '当前数据库排名', exact: true })
    await expect(panel).toContainText('同组 2 人')
    await expect(panel).toContainText(`本次成绩：第 2 名 · 得分 ${repeat.score} 分`)
    await expect(panel).toContainText(`个人历史最佳：第 2 名 · 得分 ${first.score} 分`)
    await expect(panel.getByRole('row')).toHaveCount(3)
    await expect(panel).toContainText(`本次落后 ${competitor.score - repeat.score} 分`)
    await expect(panel).toContainText('匿名参测者')
    await expect(page.getByRole('link', { name: /^继续下一项/ })).toBeVisible()
    await page.reload()
    await expect(panel).toContainText('同组 2 人')
    await logout(page)
    await expect(panel).toHaveCount(0)
    expect((await main.api.get(endpoint)).status()).toBe(401)
    expect((await main.api.get(`${PREFIX}/sessions/${repeatSession.id}`)).status()).toBe(401)
  } finally {
    await other.api.dispose()
  }
})
