import { createHash } from 'node:crypto'
import { Buffer } from 'node:buffer'
import { constants } from 'node:fs'
import { copyFile, lstat, mkdir, open, readdir, readFile, realpath, rename, writeFile } from 'node:fs/promises'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath, URL } from 'node:url'
import { TextDecoder } from 'node:util'
import { resolveBuildId } from './build-id.mjs'

const MAX_BYTES = 536870912, MAX_MANIFEST_BYTES = 16777216, MAX_ENTRIES = 100000
const schema = JSON.parse(await readFile(new URL('./web-assets.schema.json', import.meta.url)))
const keywords = new Set(['$schema', '$id', 'title', '$defs', '$ref', 'type', 'const', 'enum',
  'required', 'properties', 'additionalProperties', 'items', 'minItems', 'maxItems',
  'contains', 'allOf', 'anyOf', 'minimum', 'maximum', 'maxLength', 'pattern'])
const fail = category => { throw new Error(category) }
const matches = (value, rule) => { try { validate(value, rule); return true } catch { return false } }

// Only the committed schema's keyword subset is supported; unknown keywords fail closed.
function validate(value, rule) {
  if (Object.keys(rule).some(key => !keywords.has(key))) fail('SCHEMA')
  if (rule.$ref) {
    if (!rule.$ref.startsWith('#/$defs/') || !schema.$defs[rule.$ref.slice(8)]) fail('SCHEMA')
    validate(value, schema.$defs[rule.$ref.slice(8)])
  }
  const types = { object: x => x !== null && typeof x === 'object' && !Array.isArray(x),
    array: Array.isArray, string: x => typeof x === 'string', integer: Number.isSafeInteger, null: x => x === null }
  if (rule.type && ![rule.type].flat().some(kind => types[kind]?.(value))) fail('SCHEMA')
  if ('const' in rule && value !== rule.const) fail('SCHEMA')
  if (rule.enum && !rule.enum.includes(value)) fail('SCHEMA')
  for (const child of rule.allOf || []) validate(value, child)
  if (rule.anyOf && !rule.anyOf.some(child => matches(value, child))) fail('SCHEMA')
  if (types.object(value)) {
    const properties = rule.properties || {}
    if ((rule.required || []).some(key => !Object.hasOwn(value, key))) fail('SCHEMA')
    if (rule.additionalProperties === false && Object.keys(value).some(key => !Object.hasOwn(properties, key))) fail('SCHEMA')
    for (const key of Object.keys(value)) if (Object.hasOwn(properties, key)) validate(value[key], properties[key])
  }
  if (Array.isArray(value)) {
    if (value.length < (rule.minItems ?? 0) || value.length > (rule.maxItems ?? MAX_ENTRIES)) fail('SCHEMA')
    if (rule.contains && !value.some(item => matches(item, rule.contains))) fail('SCHEMA')
    if (rule.items) for (const item of value) validate(item, rule.items)
  }
  if (typeof value === 'string' && (value.length > (rule.maxLength ?? MAX_MANIFEST_BYTES)
      || rule.pattern && !new RegExp(rule.pattern).test(value))) fail('SCHEMA')
  if (typeof value === 'number' && (value < (rule.minimum ?? value) || value > (rule.maximum ?? value))) fail('SCHEMA')
}

function fileMap(files, maxBytes = MAX_BYTES) {
  if (files.length > MAX_ENTRIES) fail('SCHEMA')
  const result = new Map()
  let total = 0
  for (const item of files) {
    validate(item, schema.$defs.file)
    if (item.path.split('/').some(part => !part || part === '.' || part === '..') || item.path.includes('\\')) fail('SCHEMA')
    const prior = result.get(item.path)
    if (prior && (prior.sha256 !== item.sha256 || prior.size !== item.size)) fail('CONFLICT')
    if (!prior) { result.set(item.path, item); total += item.size }
    if (total > Math.min(maxBytes, MAX_BYTES)) fail('CAPACITY')
  }
  return { files: result, total }
}

function inventory(data, retained = false) {
  validate(data, retained ? schema.$defs.retainedInput : schema)
  const releases = new Set(), files = [...(data.documents || [])]
  for (const group of data.sets) {
    if (releases.has(group.release_id) || new Set(group.files.map(f => f.path)).size !== group.files.length) fail('CONFLICT')
    releases.add(group.release_id)
    if (files.length + group.files.length > MAX_ENTRIES) fail('SCHEMA')
    files.push(...group.files)
  }
  const result = fileMap(files)
  if (!retained && (result.total !== data.total_bytes || new Set(data.documents.map(f => f.path)).size !== 2)) fail('SCHEMA')
  if (Buffer.byteLength(JSON.stringify(data)) > MAX_MANIFEST_BYTES) fail('SCHEMA')
  return result
}

export function validateRetainedInput(data) { inventory(data, true); return data }

/** Bounded raw input and duplicate JSON keys are part of the cross-language contract. */
export async function readRetainedInput(filename) {
  let handle
  try {
    handle = await open(filename, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK)
    const stat = await handle.stat()
    if (!stat.isFile() || stat.nlink !== 1 || stat.size > MAX_MANIFEST_BYTES) fail('SCHEMA')
    const buffer = Buffer.alloc(Math.min(stat.size + 1, MAX_MANIFEST_BYTES + 1))
    let used = 0
    while (used < buffer.length) {
      const { bytesRead } = await handle.read(buffer, used, buffer.length - used, null)
      if (!bytesRead) break
      used += bytesRead
    }
    if (used !== stat.size) fail('SCHEMA')
    const raw = buffer.subarray(0, used)
    const data = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(raw))
    const stack = []
    for (const [token] of raw.toString('utf8').matchAll(/"(?:[^"\\]|\\.)*"|[{}[\],:]|[^\s{}[\],:]+/g)) {
      const top = stack.at(-1)
      if (token === '{') stack.push({ keys: new Set(), key: true })
      else if (token === '[') stack.push(null)
      else if (token === '}' || token === ']') stack.pop()
      else if (token === ',' && top) top.key = true
      else if (token === ':' && top) top.key = false
      else if (token.startsWith('"') && top?.key) {
        const key = JSON.parse(token)
        if (top.keys.has(key)) fail('SCHEMA')
        top.keys.add(key)
      }
      if (stack.length > 20) fail('SCHEMA')
    }
    validateRetainedInput(data)
    return { data, raw }
  } catch (error) {
    if (['SCHEMA', 'CONFLICT', 'CAPACITY'].includes(error.message)) throw error
    fail('SCHEMA')
  } finally { await handle?.close() }
}

async function checkTree(root, expected, ignored = new Set()) {
  if (!(await lstat(root)).isDirectory() || (await lstat(root)).isSymbolicLink()) fail('TREE')
  const found = new Set()
  async function walk(directory, prefix = '') {
    for (const name of await readdir(directory)) {
      const relative = prefix + name, filename = path.join(directory, name)
      const stat = await lstat(filename)
      if (stat.isSymbolicLink()) fail('TREE')
      if (stat.isDirectory()) {
        if (![...expected.keys()].some(key => key.startsWith(`${relative}/`))) fail('TREE')
        await walk(filename, `${relative}/`)
      } else if (stat.isFile() && stat.nlink === 1 && (expected.has(relative) || ignored.has(relative))) found.add(relative)
      else fail('TREE')
    }
  }
  await walk(root)
  if (found.size !== expected.size + ignored.size) fail('TREE')
  for (const item of expected.values()) {
    const handle = await open(path.join(root, item.path), constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK)
    try {
      const stat = await handle.stat()
      if (!stat.isFile() || stat.nlink !== 1) fail('TREE')
      if (stat.size !== item.size) fail('HASH')
      const hash = createHash('sha256'), buffer = Buffer.alloc(65536)
      let total = 0
      while (true) {
        const { bytesRead } = await handle.read(buffer, 0, Math.min(buffer.length, item.size - total + 1), null)
        if (!bytesRead) break
        total += bytesRead
        if (total > item.size) fail('HASH')
        hash.update(buffer.subarray(0, bytesRead))
      }
      if (total !== item.size || hash.digest('hex') !== item.sha256) fail('HASH')
    } finally { await handle.close() }
  }
}

export async function mergeRetainedAssets({ currentManifest, retainedDir, outputDir, metadataDir, maxBytes = MAX_BYTES }) {
  try {
    const current = inventory(currentManifest)
    const { data: input, raw } = await readRetainedInput(path.join(retainedDir, 'manifest.json'))
    if (input.candidate_release_id !== null && input.candidate_release_id !== currentManifest.release_id) fail('SCHEMA')
    if (input.sets.some(group => group.release_id === currentManifest.release_id)) fail('CONFLICT')
    const retained = inventory(input, true)
    const combined = fileMap([...current.files.values(), ...retained.files.values()], maxBytes)
    const metadata = await realpath(metadataDir), output = await realpath(outputDir), historical = await realpath(retainedDir)
    for (const other of [metadata, historical]) {
      const relative = path.relative(output, other)
      if (relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative))) fail('TREE')
    }
    // All input validation, aggregate capacity and byte verification precede writes.
    await checkTree(output, current.files)
    await checkTree(historical, retained.files, new Set(['manifest.json']))
    for (const item of retained.files.values()) if (!current.files.has(item.path)) {
      const destination = path.join(output, item.path)
      await mkdir(path.dirname(destination), { recursive: true })
      await copyFile(path.join(historical, item.path), destination, constants.COPYFILE_EXCL)
    }
    await checkTree(output, combined.files)
    const result = { ...currentManifest, predecessor_image_id: input.predecessor_image_id,
      input_sha256: createHash('sha256').update(raw).digest('hex'), total_bytes: combined.total,
      sets: [...currentManifest.sets, ...input.sets] }
    inventory(result)
    const filename = path.join(metadata, 'assets-manifest.json')
    await writeFile(`${filename}.tmp`, `${JSON.stringify(result, null, 2)}\n`, { mode: 0o600, flag: 'wx' })
    await rename(`${filename}.tmp`, filename)
    return result
  } catch (error) {
    if (['SCHEMA', 'CONFLICT', 'CAPACITY', 'TREE', 'HASH'].includes(error.message)) throw error
    fail('TREE')
  }
}

/**
 * Inventory only current output. Retention/merge is a separate release operation.
 * @param {{distDir: string, metadataDir: string, buildId: string, releaseId: string}} options
 */
export async function writeCurrentAssets({ distDir, metadataDir, buildId, releaseId }) {
  resolveBuildId({ root: distDir, mode: 'production', explicitId: buildId })
  resolveBuildId({ root: distDir, mode: 'production', explicitId: releaseId })
  const dist = await realpath(distDir)
  await mkdir(metadataDir, { recursive: true })
  const metadata = await realpath(metadataDir)
  const relative = path.relative(dist, metadata)
  if (relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative))) {
    throw new Error('Private metadata directory must be outside public output')
  }
  const info = `${JSON.stringify({ schema_version: 1, build_id: buildId })}\n`
  // Never follow a preexisting output symlink when creating the public document.
  try {
    if ((await lstat(path.join(dist, 'build-info.json'))).isSymbolicLink()) {
      throw new Error('Public document symlink refused')
    }
  } catch (error) {
    if (error.code !== 'ENOENT') throw error
  }
  await writeFile(path.join(dist, 'build-info.json'), info)
  const documents = [], files = []
  async function inventory(directory, prefix = '') {
    for (const name of (await readdir(directory)).sort()) {
      const relativePath = prefix + name
      const absolute = path.join(directory, name)
      const stat = await lstat(absolute)
      if (stat.isSymbolicLink()) throw new Error(`Public asset symlink refused: ${relativePath}`)
      if (stat.isDirectory()) {
        if (relativePath !== 'assets' && !relativePath.startsWith('assets/')) {
          throw new Error(`Unapproved public directory: ${relativePath}`)
        }
        await inventory(absolute, `${relativePath}/`)
        continue
      }
      if (!stat.isFile()) throw new Error(`Unsupported public artifact: ${relativePath}`)
      const document = ['index.html', 'build-info.json'].includes(relativePath)
      if (!document && (!/^assets\/(?:[A-Za-z0-9_@+=,-][A-Za-z0-9_@+=,.-]*\/)*[A-Za-z0-9_@+=,-][A-Za-z0-9_@+=,.-]*\.(?:js|css|png|jpe?g|gif|svg|ico|webp|avif|woff2?|ttf|otf|eot|wasm|json)$/.test(relativePath)
        || relativePath.split('/').some(part => part === '..' || part === '.'))) {
        throw new Error(`Unapproved public asset/artifact: ${relativePath}`)
      }
      const bytes = await readFile(absolute)
      const record = { path: relativePath, size: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex') }
      ;(document ? documents : files).push(record)
    }
  }
  await inventory(dist)
  if (!documents.some(document => document.path === 'index.html')) throw new Error('Current public index.html is required')
  const manifest = {
    schema_version: 1, build_id: buildId, release_id: releaseId,
    predecessor_image_id: null, input_sha256: null,
    total_bytes: [...documents, ...files].reduce((sum, file) => sum + file.size, 0),
    documents,
    sets: [{ release_id: releaseId, build_id: buildId, superseded_by_release_id: null, files }],
  }
  const output = path.join(metadata, 'assets-manifest.json')
  await writeFile(`${output}.tmp`, `${JSON.stringify(manifest, null, 2)}\n`, { mode: 0o600, flag: 'wx' })
  await rename(`${output}.tmp`, output)
  return manifest
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (process.argv.length !== 4 || process.argv[2] !== '--merge-retained') fail('SCHEMA')
  const metadataDir = path.resolve('.build-metadata'), outputDir = path.resolve('dist')
  const currentManifest = JSON.parse(await readFile(path.join(metadataDir, 'assets-manifest.json'), 'utf8'))
  await mergeRetainedAssets({ currentManifest, retainedDir: path.resolve(process.argv[3]), outputDir, metadataDir, maxBytes: MAX_BYTES })
}
