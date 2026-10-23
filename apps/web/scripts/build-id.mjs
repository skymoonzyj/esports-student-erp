import { Buffer } from 'node:buffer'
import { createHash } from 'node:crypto'
import { lstatSync, readdirSync, readFileSync } from 'node:fs'
import path from 'node:path'

/** @param {{root: string, mode: string, explicitId?: string}} options */
export function resolveBuildId({ root, mode, explicitId }) {
  if (mode === 'development') return 'development'
  if (explicitId !== undefined) {
    if (!/^[A-Za-z0-9][A-Za-z0-9_-]{0,199}$/.test(explicitId)) {
      throw new Error('Invalid web build ID')
    }
    return explicitId
  }
  const files = []
  function collect(relative) {
    if (relative.split('/').some(part => ['dist', '.build-metadata', '.asset-history'].includes(part))) return
    const absolute = path.join(root, relative)
    const stat = lstatSync(absolute)
    if (stat.isSymbolicLink()) throw new Error(`Build input symlink refused: ${relative}`)
    if (stat.isDirectory()) {
      for (const name of readdirSync(absolute)) collect(`${relative}/${name}`)
    } else if (stat.isFile()) files.push(relative)
    else throw new Error(`Unsupported build input: ${relative}`)
  }
  for (const name of readdirSync(root)) {
    if (['src', 'public', 'index.html', 'package.json', 'package-lock.json',
      'npm-shrinkwrap.json', 'pnpm-lock.yaml', 'yarn.lock', 'bun.lock', 'bun.lockb'].includes(name)
      || /^(?:vite\.config\.[cm]?[jt]s|tsconfig(?:\.[\w-]+)?\.json)$/.test(name)) collect(name)
  }
  // Path and content lengths prevent ambiguous byte concatenations.
  const hash = createHash('sha256')
  for (const name of files.sort()) {
    const content = readFileSync(path.join(root, name))
    hash.update(`${Buffer.byteLength(name)}:${name}:${content.length}:`)
    hash.update(content)
  }
  return `local-${hash.digest('hex')}`
}
