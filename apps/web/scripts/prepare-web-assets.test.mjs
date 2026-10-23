import assert from 'node:assert/strict'
import { Buffer } from 'node:buffer'
import { createHash } from 'node:crypto'
import { mkdtemp, mkdir, readFile, writeFile, rm, symlink, realpath } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import process from 'node:process'
import test from 'node:test'
import { fileURLToPath, URL } from 'node:url'
import { runInNewContext } from 'node:vm'

const modules = await Promise.allSettled([
  import('./build-id.mjs'), import('./prepare-web-assets.mjs'),
])
const resolveBuildId = (...args) => {
  assert.equal(modules[0].status, 'fulfilled', 'build identity module must load')
  return modules[0].value.resolveBuildId(...args)
}
const writeCurrentAssets = (...args) => {
  assert.equal(modules[1].status, 'fulfilled', 'asset inventory module must load')
  return modules[1].value.writeCurrentAssets(...args)
}
const mergeRetainedAssets = (...args) => {
  assert.equal(typeof modules[1].value.mergeRetainedAssets, 'function', 'retained merger required')
  return modules[1].value.mergeRetainedAssets(...args)
}
async function temporary(t) {
  const root = await realpath(await mkdtemp(path.join(os.tmpdir(), 'erp-assets-')))
  t.after(() => rm(root, { recursive: true, force: true }))
  return root
}
async function file(root, name, body) {
  await mkdir(path.dirname(path.join(root, name)), { recursive: true })
  await writeFile(path.join(root, name), body)
}

// Break caught: nondeterministic traversal or omitted selected input bytes.
test('local identity sorts the same file inventory and changes with source/config/lock bytes', async (t) => {
  const a = await temporary(t), b = await temporary(t)
  const inputs = [['src/z.ts', 'z'], ['src/a.ts', 'a'], ['public/icon.svg', '<svg/>'],
    ['index.html', 'html'], ['package.json', '{}'], ['package-lock.json', '{}'],
    ['vite.config.ts', 'config'], ['tsconfig.app.json', '{}']]
  for (const [name, body] of inputs) await file(a, name, body)
  for (const [name, body] of [...inputs].reverse()) await file(b, name, body)
  const identity = resolveBuildId({ root: a, mode: 'production' })
  assert.match(identity, /^local-[a-f0-9]{64}$/)
  assert.equal(resolveBuildId({ root: b, mode: 'production' }), identity)
  for (const [name, body] of inputs) {
    await file(a, name, `${body}changed`)
    assert.notEqual(resolveBuildId({ root: a, mode: 'production' }), identity, name)
    await file(a, name, body)
  }
  for (const name of ['dist/assets/old.js', '.build-metadata/assets-manifest.json',
    '.asset-history/assets/old.js', 'node_modules/ignored.js', '.env']) await file(a, name, 'ignored')
  assert.equal(resolveBuildId({ root: a, mode: 'production' }), identity)
})

test('explicit release identity is validated and development has a fixed identity', async (t) => {
  const root = await temporary(t)
  assert.equal(resolveBuildId({ root, mode: 'development', explicitId: 'release-1' }), 'development')
  assert.equal(resolveBuildId({ root, mode: 'production', explicitId: '20261018T120000Z-abc' }), '20261018T120000Z-abc')
  for (const explicitId of ['', '../escape', 'space id', '<script>', 'a\n']) {
    assert.throws(() => resolveBuildId({ root, mode: 'production', explicitId }), /build.*id/i)
  }
})

test('local identity refuses symlinked source inputs', async (t) => {
  const root = await temporary(t)
  await file(root, 'secret.txt', 'not an input')
  await mkdir(path.join(root, 'src'))
  await symlink(path.join(root, 'secret.txt'), path.join(root, 'src/link.ts'))
  assert.throws(() => resolveBuildId({ root, mode: 'production' }), /symlink/i)
})

test('local identity excludes generated and historical directories even inside selected trees', async (t) => {
  const root = await temporary(t)
  await file(root, 'src/main.ts', 'source')
  await mkdir(path.join(root, 'public'))
  const before = resolveBuildId({ root, mode: 'production' })
  for (const name of ['src/dist/out.js', 'src/.build-metadata/private.json', 'public/.asset-history/assets/old.js']) {
    await file(root, name, 'output, not source')
  }
  assert.equal(resolveBuildId({ root, mode: 'production' }), before)
})

test('asset inventory requires a current document and validates release identities', async (t) => {
  const root = await temporary(t), distDir = path.join(root, 'dist'), metadataDir = path.join(root, 'private')
  await mkdir(distDir)
  await assert.rejects(writeCurrentAssets({ distDir, metadataDir, buildId: 'r1', releaseId: 'r1' }), /index.html/i)
  await file(distDir, 'index.html', 'html')
  await assert.rejects(writeCurrentAssets({ distDir, metadataDir, buildId: 'r1', releaseId: '../private' }), /build.*id/i)
})

// Break caught: omitted bytes, HTML in historic sets, or private metadata in docroot.
test('inventory contains current documents and one canonical asset set with exact bytes', async (t) => {
  const root = await temporary(t), distDir = path.join(root, 'dist'), metadataDir = path.join(root, 'private')
  await file(distDir, 'index.html', '<html>current</html>')
  await file(distDir, 'assets/a.js', 'hello')
  await file(distDir, 'assets/nested/b.css', 'css')
  const manifest = await writeCurrentAssets({ distDir, metadataDir, buildId: 'release-1', releaseId: 'release-1' })
  const info = await readFile(path.join(distDir, 'build-info.json'))
  assert.deepEqual(JSON.parse(info), { schema_version: 1, build_id: 'release-1' })
  assert.deepEqual(manifest.documents.map(f => f.path), ['build-info.json', 'index.html'])
  assert.deepEqual(manifest.sets, [{ release_id: 'release-1', build_id: 'release-1', superseded_by_release_id: null,
    files: [{ path: 'assets/a.js', size: 5, sha256: '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824' },
      { path: 'assets/nested/b.css', size: 3, sha256: createHash('sha256').update('css').digest('hex') }] }])
  assert.equal(manifest.total_bytes, Buffer.byteLength('<html>current</html>') + info.length + 8)
  assert.equal(manifest.predecessor_image_id, null)
  assert.equal(manifest.input_sha256, null)
  assert.equal(manifest.schema_version, 1)
  assert.deepEqual(JSON.parse(await readFile(path.join(metadataDir, 'assets-manifest.json'))), manifest)
  await assert.rejects(readFile(path.join(distDir, 'assets-manifest.json')), { code: 'ENOENT' })
})

test('inventory refuses unaccounted public files, forbidden artifacts and symlinks', async (t) => {
  for (const name of ['50x.html', 'other.json', '.env', 'source.ts', 'assets/app.js.map',
    'assets/src.ts', 'assets/.env', 'assets/error.log', 'assets/../oops.js', 'assets/back\\slash.js']) {
    const root = await temporary(t), distDir = path.join(root, 'dist')
    await file(distDir, 'index.html', 'html')
    await file(distDir, name, 'unsafe')
    await assert.rejects(writeCurrentAssets({ distDir, metadataDir: path.join(root, 'private'), buildId: 'r1', releaseId: 'r1' }), /public|asset|artifact/i, name)
  }
  const root = await temporary(t), distDir = path.join(root, 'dist')
  await file(distDir, 'index.html', 'html')
  await mkdir(path.join(distDir, 'assets'))
  await symlink(path.join(distDir, 'index.html'), path.join(distDir, 'assets/link.js'))
  await assert.rejects(writeCurrentAssets({ distDir, metadataDir: path.join(root, 'private'), buildId: 'r1', releaseId: 'r1' }), /symlink/i)
  await assert.rejects(writeCurrentAssets({ distDir, metadataDir: path.join(distDir, 'private'), buildId: 'r1', releaseId: 'r1' }), /metadata/i)
})

test('private manifest writing refuses an existing temporary symlink without corrupting public HTML', async (t) => {
  const root = await temporary(t), distDir = path.join(root, 'dist'), metadataDir = path.join(root, 'private')
  await file(distDir, 'index.html', 'current html')
  await mkdir(metadataDir)
  await symlink(path.join(distDir, 'index.html'), path.join(metadataDir, 'assets-manifest.json.tmp'))
  await assert.rejects(writeCurrentAssets({ distDir, metadataDir, buildId: 'r1', releaseId: 'r1' }), /exist|symlink/i)
  assert.equal(await readFile(path.join(distDir, 'index.html'), 'utf8'), 'current html')
})

test('actual Vite config compiles the same identity as its public document', async (t) => {
  const { build } = await import('vite')
  const root = await temporary(t)
  await file(root, 'index.html', '<html><head></head><body><script type="module" src="/src/main.js"></script></body></html>')
  await file(root, 'src/main.js', 'globalThis.compiledId = __ERP_BUILD_ID__')
  const original = process.cwd()
  process.chdir(root)
  t.after(() => process.chdir(original))
  await build({ configFile: fileURLToPath(new URL('../vite.config.ts', import.meta.url)), root, logLevel: 'silent', build: { modulePreload: false } })
  const info = JSON.parse(await readFile(path.join(root, 'dist/build-info.json')))
  const manifest = JSON.parse(await readFile(path.join(root, '.build-metadata/assets-manifest.json')))
  const script = manifest.sets[0].files.find(f => f.path.endsWith('.js'))
  const compiled = await readFile(path.join(root, 'dist', script.path), 'utf8')
  const context = {}
  runInNewContext(compiled, context)
  assert.equal(context.compiledId, info.build_id)
  assert.match(await readFile(path.join(root, 'dist/index.html'), 'utf8'), new RegExp(`name="erp-build-id" content="${info.build_id}"`))
})

async function mergeFixture(t) {
  const root = await temporary(t), outputDir = path.join(root, 'dist'), metadataDir = path.join(root, 'private')
  const retainedDir = path.join(root, 'retained')
  await file(outputDir, 'index.html', 'NEW HTML')
  await file(outputDir, 'assets/new.js', 'new entry')
  await file(outputDir, 'assets/shared.js', 'shared')
  const currentManifest = await writeCurrentAssets({ distDir: outputDir, metadataDir, buildId: 'candidate', releaseId: 'candidate' })
  const bodies = [['assets/old.js', 'import "./shared.js";import "./old.css"'], ['assets/shared.js', 'shared'], ['assets/old.css', 'old css']]
  const files = bodies.map(([name, body]) => ({ path: name, size: Buffer.byteLength(body), sha256: createHash('sha256').update(body).digest('hex') }))
  const input = { schema_version: 1, candidate_release_id: 'candidate', predecessor_image_id: `sha256:${'b'.repeat(64)}`,
    sets: [{ release_id: 'previous', build_id: 'previous', superseded_by_release_id: 'candidate', files }] }
  for (const [name, body] of bodies) await file(retainedDir, name, body)
  await file(retainedDir, 'manifest.json', JSON.stringify(input))
  return { currentManifest, outputDir, metadataDir, retainedDir, input }
}

// Break caught: retaining only entrypoints, counting shared bytes twice, or replacing current HTML.
test('merge_keeps_dependency_closure', async (t) => {
  const options = await mergeFixture(t)
  const result = await mergeRetainedAssets({ ...options, maxBytes: 536870912 })
  for (const name of ['old.js', 'shared.js', 'old.css', 'new.js']) assert.ok(await readFile(path.join(options.outputDir, 'assets', name)))
  assert.equal(await readFile(path.join(options.outputDir, 'index.html'), 'utf8'), 'NEW HTML')
  assert.equal(result.sets.length, 2)
  assert.equal(result.predecessor_image_id, options.input.predecessor_image_id)
  assert.equal(result.input_sha256, createHash('sha256').update(JSON.stringify(options.input)).digest('hex'))
  assert.equal(result.total_bytes, options.currentManifest.total_bytes + options.input.sets[0].files.filter(f => f.path !== 'assets/shared.js').reduce((n, f) => n + f.size, 0))
  assert.deepEqual(JSON.parse(await readFile(path.join(options.metadataDir, 'assets-manifest.json'))), result)
})

test('merge_rejects_conflict_and_overflow', async (t) => {
  for (const kind of ['conflict', 'overflow', 'unknown', 'symlink', 'tamper', 'candidate', 'duplicate']) {
    const options = await mergeFixture(t)
    if (kind === 'conflict') options.input.sets[0].files.find(f => f.path === 'assets/shared.js').sha256 = 'c'.repeat(64)
    if (kind === 'unknown') await file(options.retainedDir, 'index.html', 'OLD HTML')
    if (kind === 'symlink') { await rm(path.join(options.retainedDir, 'assets/old.css')); await symlink(path.join(options.outputDir, 'index.html'), path.join(options.retainedDir, 'assets/old.css')) }
    if (kind === 'tamper') await file(options.retainedDir, 'assets/old.css', 'tampered')
    if (kind === 'candidate') options.input.candidate_release_id = 'wrong'
    if (kind === 'duplicate') options.input.sets[0].files.push(options.input.sets[0].files[0])
    await file(options.retainedDir, 'manifest.json', JSON.stringify(options.input))
    await assert.rejects(mergeRetainedAssets({ ...options, maxBytes: kind === 'overflow' ? options.currentManifest.total_bytes : 536870912 }), /^(?:Error: )?(CONFLICT|CAPACITY|TREE|HASH|SCHEMA)$/)
    assert.equal(await readFile(path.join(options.outputDir, 'index.html'), 'utf8'), 'NEW HTML')
    await assert.rejects(readFile(path.join(options.outputDir, 'assets/old.js')), { code: 'ENOENT' })
  }
})

test('retained input shares the Python schema fixture and never accepts final manifest', async (t) => {
  const options = await mergeFixture(t)
  const fixture = JSON.parse(await readFile(new URL('./fixtures/web-assets-contract.json', import.meta.url)))
  const { validateRetainedInput } = modules[1].value
  assert.equal(typeof validateRetainedInput, 'function', 'shared retained schema validator required')
  for (const item of fixture.valid) assert.doesNotThrow(() => validateRetainedInput(item))
  for (const item of [...fixture.invalid, options.currentManifest]) assert.throws(() => validateRetainedInput(item), /^Error: SCHEMA$/)
  await file(options.retainedDir, 'manifest.json', ' '.repeat(16777217))
  await assert.rejects(mergeRetainedAssets({ ...options, maxBytes: 536870912 }), /^Error: SCHEMA$/)
})

test('bounded retained reader rejects malformed JSON and duplicate escaped keys with stable category', async (t) => {
  const root = await temporary(t), filename = path.join(root, 'manifest.json')
  const { readRetainedInput } = modules[1].value
  for (const body of ['{', '{"schema_version":1,"schema_versi\\u006fn":1,"candidate_release_id":null,"predecessor_image_id":null,"sets":[]}']) {
    await writeFile(filename, body)
    await assert.rejects(readRetainedInput(filename), /^Error: SCHEMA$/)
  }
})

test('retained schema enforces exact capacity, ASCII path and entry-count boundaries', async () => {
  const { validateRetainedInput } = modules[1].value
  const fixture = JSON.parse(await readFile(new URL('./fixtures/web-assets-contract.json', import.meta.url)))
  const valid = fixture.valid[1]
  const clone = () => JSON.parse(JSON.stringify(valid))
  const input = clone()
  input.sets[0].files[0].size = 536870912
  assert.doesNotThrow(() => validateRetainedInput(input))
  input.sets[0].files.push({ ...input.sets[0].files[0], path: 'assets/extra.js', size: 1 })
  assert.throws(() => validateRetainedInput(input), /^Error: CAPACITY$/)
  for (const name of ['assets/a.js\n', 'assets/é.js', `assets/${'a'.repeat(1015)}.js`, '/assets/a.js', 'assets/./a.js']) {
    const invalid = clone()
    invalid.sets[0].files[0].path = name
    assert.throws(() => validateRetainedInput(invalid), /^Error: SCHEMA$/)
  }
  const pathBoundary = clone()
  pathBoundary.sets[0].files[0].path = `assets/${'a'.repeat(1014)}.js`
  assert.doesNotThrow(() => validateRetainedInput(pathBoundary))
  assert.throws(() => validateRetainedInput({ ...valid, sets: Array(100001).fill(valid.sets[0]) }), /^Error: SCHEMA$/)
})
