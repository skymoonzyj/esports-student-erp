import { createHash } from 'node:crypto'
import { expect, test, type Page, type TestInfo } from '@playwright/test'
import { E2E_CREDENTIALS } from './e2eCredentials'

const games = [
  ['reactiontime', '反应速度', '反应时间测试区域'], ['aim', '瞄准速度', '点击目标开始'],
  ['sequence', '顺序记忆', '开始测试'], ['number-memory', '数字记忆', '开始数字记忆'],
  ['verbal-memory', '中文词语记忆', '开始测试'], ['chimp', '黑猩猩测试', '开始黑猩猩测试'],
  ['memory', '视觉记忆', '开始视觉记忆'], ['dynamic-vision', '动态视力', '开始测试'],
] as const

async function setup(page: Page, info: TestInfo) {
  const payload = info.config.metadata.talentGames
  const expected = payload.gameAssets as Record<string, { path: string; size: number; sha256: string }[]>
  const paths = new Set(Object.values(expected).flatMap(items => items.map(item => '/' + item.path)))
  const requested = new Set<string>()
  const bodies = new Map<string, Promise<{ size: number; hash: string; status: number }>>()
  page.on('request', request => { const path = new URL(request.url()).pathname; if (paths.has(path)) requested.add(path) })
  page.on('response', response => {
    const path = new URL(response.url()).pathname
    if (paths.has(path)) bodies.set(path, response.body().then(body => ({ size: body.length, hash: createHash('sha256').update(body).digest('hex'), status: response.status() })))
  })
  await page.goto('/login')
  await page.getByLabel('手机号或邮箱', { exact: true }).fill(E2E_CREDENTIALS.student.identifier)
  await page.getByLabel('密码', { exact: true }).fill(E2E_CREDENTIALS.student.password)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page.getByRole('heading', { name: '工作台', exact: true })).toBeVisible()
  return { expected, requested, bodies }
}

for (const [id, name, start] of games) test(`cold selected resources: ${id}`, async ({ page }, info) => {
  const { expected, requested, bodies } = await setup(page, info)
  await page.goto('/talent-tests')
  await expect(page.getByRole('heading', { name: '天赋测试', exact: true })).toBeVisible()
  // The resource-only check does not begin games, create answers, or save results.
  await page.getByRole('button', { name: `选择${name}`, exact: true }).or(page.getByRole('link', { name: `选择${name}`, exact: true })).click()
  await expect(page.getByRole('button', { name: start, exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: start, exact: true })).toBeEnabled()
  await expect.poll(() => [...bodies.keys()].filter(path => expected[id].some(item => '/' + item.path === path)).length).toBe(2)
  for (const asset of expected[id]) {
    expect(requested.has('/' + asset.path)).toBe(true)
    const body = await bodies.get('/' + asset.path)!
    expect(body).toEqual({ size: asset.size, hash: asset.sha256, status: 200 })
  }
  const otherRequests = Object.entries(expected).filter(([other]) => other !== id).flatMap(([, assets]) => assets).filter(asset => requested.has('/' + asset.path)).length
  expect(otherRequests).toBe(0)
  info.annotations.push({ type: 'talent-resource', description: JSON.stringify({ game: id, selectedAssets: 2, otherGameAssets: otherRequests, servedHashesMatch: true }) })
})

test('cold history imports no game resources', async ({ page }, info) => {
  const { requested } = await setup(page, info)
  await page.goto('/talent-tests/history')
  await expect(page.getByRole('heading', { name: '天赋测试历史', exact: true })).toBeVisible()
  await expect(page.getByRole('link', { name: '查看报告' }).first()).toBeVisible()
  expect(requested.size).toBe(0)
  info.annotations.push({ type: 'talent-resource', description: JSON.stringify({ view: 'history', gameAssets: 0 }) })
})

test('cold authorized report imports no game resources', async ({ page }, info) => {
  const { requested } = await setup(page, info)
  const response = await page.request.get('/api/v1/talent-tests/reports?page=1&page_size=20')
  expect(response.status()).toBe(200)
  const report = (await response.json()).items[0]
  expect(Boolean(report?.id)).toBe(true)
  await page.goto(`/talent-tests/reports/${report.id}`)
  await expect(page.getByRole('heading', { name: '训练参考报告', exact: true })).toBeVisible()
  expect(requested.size).toBe(0)
  info.annotations.push({ type: 'talent-resource', description: JSON.stringify({ view: 'report', gameAssets: 0 }) })
})
