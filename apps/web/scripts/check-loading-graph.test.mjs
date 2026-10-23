import { test } from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, writeFile, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { inspectSharedStyleOwnership } from './check-loading-graph.mjs'
import * as graphTools from './check-loading-graph.mjs'

async function check(graph, manifest = { 'index.html': { file: 'assets/index.js', isEntry: true } }) {
  const { checkInitialGraph } = await import('./check-loading-graph.mjs')
  const root = await mkdtemp(path.join(os.tmpdir(), 'loading-graph-'))
  try {
    const manifestPath = path.join(root, 'manifest.json'), bundleGraphPath = path.join(root, 'graph.json')
    await writeFile(manifestPath, JSON.stringify(manifest))
    await writeFile(bundleGraphPath, JSON.stringify({ schemaVersion: 1, chunks: graph }))
    return await checkInitialGraph({ manifestPath, bundleGraphPath })
  } finally { await rm(root, { recursive: true }) }
}
const chunk = (moduleIds, imports = [], dynamicImports = []) => ({ moduleIds, imports, dynamicImports })
test('follows opaque static chunk edges and rejects actual business modules', async () => {
  const result = await check({ 'assets/index.js': chunk(['/src/main.tsx'], ['assets/x.js']), 'assets/x.js': chunk(['/src/people/PeoplePage.tsx']) })
  assert.equal(result.ok, false)
  assert.deepEqual(result.forbiddenModules, ['/src/people/PeoplePage.tsx'])
})
test('only static closure counts; lazy pages are allowed and explicit lightweight contracts remain', async () => {
  const result = await check({ 'assets/index.js': chunk(['/src/main.tsx', '/src/talentTests/contracts.ts', '/src/housing/housingKeys.ts'], [], ['assets/x.js']), 'assets/x.js': chunk(['/src/talentTests/GameHost.tsx']) })
  assert.equal(result.ok, true)
  assert.deepEqual(result.initialChunks, ['assets/index.js'])
})
for (const moduleId of ['/src/housing/HousingOverviewPage.tsx', '/src/procurement/ProcurementPage.tsx', '/src/evaluations/EvaluationTaskDetail.tsx', '/src/talentTests/engine/reaction.ts', '/src/esportsAssessments/hostState.ts']) {
  test(`rejects implementation despite innocuous chunk name: ${moduleId}`, async () => {
    assert.equal((await check({ 'assets/index.js': chunk([moduleId]) })).ok, false)
  })
}
test('fails closed for missing static edges instead of silently approving incomplete metadata', async () => {
  await assert.rejects(check({ 'assets/index.js': chunk(['/src/main.tsx'], ['missing.js']) }), /missing/i)
})

const iconNames = ['download', 'filter', 'pencil', 'trash-2', 'activity', 'calendar-days', 'chevron-down',
  'message-square-text', 'plane-takeoff', 'wallet-cards', 'mail', 'shield-check', 'sliders-horizontal', 'user-plus', 'user-round', 'x']
const iconId = name => `/project/node_modules/lucide-react/dist/esm/icons/${name}.js`

test('authenticated icon leaves cannot enter the initial static closure under an opaque name', async () => {
  const result = await check({ 'assets/index.js': chunk(['/src/main.tsx'], ['assets/opaque.js']),
    'assets/opaque.js': chunk([iconId('download')]) })
  assert.equal(result.ok, false)
  assert.deepEqual(result.forbiddenModules, [iconId('download')])
})

test('finite icon selector captures only the sixteen exact leaves, not helpers, other icons, sources or suffixes', () => {
  assert.equal(typeof graphTools.isAuthenticatedIconModule, 'function')
  for (const name of iconNames) assert.equal(graphTools.isAuthenticatedIconModule(iconId(name)), true, name)
  for (const id of [iconId('eye'), iconId('eye-off'), iconId('gamepad-2'), iconId('clipboard-pen-line'),
    iconId('landmark'), iconId('file-spreadsheet'), iconId('download') + '?raw', iconId('download') + '.map',
    '/project/src/icons/download.js', '/project/node_modules/lucide-react/dist/esm/createLucideIcon.js',
    '/project/node_modules/lucide-react/dist/esm/Icon.js', '/project/node_modules/react/index.js', '/src/auth/AuthProvider.tsx']) {
    assert.equal(graphTools.isAuthenticatedIconModule(id), false, id)
  }
})

function iconFixture() {
  return { graph: { chunks: {
    'assets/index.js': chunk(['/src/main.tsx', '/project/node_modules/lucide-react/dist/esm/createLucideIcon.js']),
    'assets/icons.js': chunk(iconNames.map(iconId), ['assets/index.js']),
    'assets/page.js': chunk(['/src/housing/HousingOverviewPage.tsx'], ['assets/icons.js', 'assets/index.js']),
  } }, manifest: { 'index.html': { file: 'assets/index.js', isEntry: true },
    '_icons.js': { file: 'assets/icons.js' }, 'src/housing/HousingOverviewPage.tsx': { file: 'assets/page.js' } },
  initialChunks: ['assets/index.js'], files: ['assets/index.js', 'assets/icons.js', 'assets/page.js'] }
}
test('actual icon group guard rejects split, missing, extra, initial, ghost, moved dependency and cyclic output', () => {
  assert.equal(typeof graphTools.inspectAuthenticatedIcons, 'function')
  assert.equal(graphTools.inspectAuthenticatedIcons(iconFixture()).ok, true)
  const changes = [
    f => { f.graph.chunks['assets/icons.js'].moduleIds.pop() },
    f => { f.graph.chunks['assets/icons.js'].moduleIds.push('/project/node_modules/lucide-react/dist/esm/Icon.js') },
    f => { f.graph.chunks['assets/icons.js'].moduleIds.push(iconId('eye')) },
    f => { f.graph.chunks['assets/extra.js'] = chunk([f.graph.chunks['assets/icons.js'].moduleIds.pop()]) },
    f => { f.graph.chunks['assets/extra.js'] = chunk([iconId('download')]) },
    f => { f.initialChunks.push('assets/icons.js') },
    f => { delete f.manifest['_icons.js'] },
    f => { f.files = f.files.filter(file => file !== 'assets/icons.js') },
    f => { f.graph.chunks['assets/icons.js'].imports.push('assets/page.js') },
    f => { f.graph.chunks['assets/icons.js'].imports.push('missing.js') },
    f => { f.graph.chunks['assets/index.js'].imports.push('assets/icons.js') },
    f => { f.graph.chunks['assets/page.js'].imports.push('assets/page.js') },
  ]
  for (const change of changes) {
    const fixture = iconFixture(); change(fixture)
    assert.equal(graphTools.inspectAuthenticatedIcons(fixture).ok, false)
  }
})

test('icon graph guard rejects a second package copy replacing a leaf or duplicating auth identity', () => {
  const leaf = iconFixture()
  leaf.graph.chunks['assets/icons.js'].moduleIds[0] = '/nested' + iconId('filter')
  assert.equal(graphTools.inspectAuthenticatedIcons(leaf).ok, false)
  const identity = iconFixture()
  identity.graph.chunks['assets/index.js'].moduleIds.push('/project/src/auth/AuthProvider.tsx')
  identity.graph.chunks['assets/page.js'].moduleIds.push('/second/src/auth/AuthProvider.tsx')
  assert.equal(graphTools.inspectAuthenticatedIcons(identity).ok, false)
})

function stylesFixture() {
  return {
    manifest: {
      'index.html': { file: 'index.js', isEntry: true, css: ['global.css'] },
      'src/people/A.tsx': { file: 'a.js', imports: ['index.html'], css: ['opaque.css'] },
      'src/people/B.tsx': { file: 'b.js', imports: ['index.html'], dynamicImports: ['src/nested.tsx'] },
      'src/nested.tsx': { file: 'nested.js', css: ['opaque.css'] },
    },
    cssAssets: { 'opaque.css': ['src/people/people.css'], 'global.css': ['src/styles/global.css'] },
    entries: { A: 'src/people/A.tsx', B: 'src/people/B.tsx' },
    groups: { people: 'src/people/people.css' }, owners: { A: ['people'] },
  }
}
test('shared stylesheet coverage follows nested emitted imports and rejects an unowned second consumer', () => {
  assert.equal(inspectSharedStyleOwnership(stylesFixture()).ok, false)
})
test('all actual consumers await the exact declared style owner', () => {
  const f = stylesFixture(); f.owners.B = ['people']
  assert.equal(inspectSharedStyleOwnership(f).ok, true)
})
test('an explicitly gated nested entry owns its styles without blocking its parent', () => {
  const f = stylesFixture(); f.entries.Panel = 'src/nested.tsx'; f.owners.Panel = ['people']
  assert.equal(inspectSharedStyleOwnership(f).ok, true)
  delete f.owners.Panel
  assert.equal(inspectSharedStyleOwnership(f).ok, false)
})
test('a broad unrelated gate cannot mask a missing shared owner or add unrelated eager CSS', () => {
  const f = stylesFixture(); f.groups.unrelated = 'src/settings/settings.css'; f.cssAssets['other.css'] = ['src/settings/settings.css']; f.owners.B = ['unrelated']
  assert.equal(inspectSharedStyleOwnership(f).ok, false)
})
test('completed shell ownership does not exempt a new independent public consumer', () => {
  const f = stylesFixture(); f.cssAssets['opaque.css'] = ['src/layout/AppShell.css']; f.entries.AppShell = f.entries.A; f.entries.PortalDashboardPage = f.entries.B; delete f.entries.A; delete f.entries.B; f.owners = {}
  assert.equal(inspectSharedStyleOwnership(f).ok, true)
  f.entries.PublicPage = 'src/people/A.tsx'
  assert.equal(inspectSharedStyleOwnership(f).ok, false)
})
