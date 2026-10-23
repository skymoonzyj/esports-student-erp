import { lstatSync, readFileSync } from 'node:fs'

export function readPrivateJson(file: string) {
  const stat = lstatSync(file)
  if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1 || (stat.mode & 0o077) || stat.uid !== process.getuid?.()) throw new Error('Private owned file required')
  return JSON.parse(readFileSync(file, 'utf8'))
}

export function talentOwnedConfig() {
  const file = process.env.FRONTEND_NAMED_CONFIG
  if (!file) return null
  const payload = readPrivateJson(file)
  if (payload.check !== 'talent-games') return null
  const url = new URL(payload.baseURL)
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error('Owned talent origin required')
  if (!Number.isInteger(payload.entry) || payload.entry < 0 || payload.entry > 6 || !['existing', 'resources', 'history'].includes(payload.mode) || (payload.mode !== 'existing' && payload.entry !== 0)) throw new Error('Fixed talent entry required')
  return payload
}
