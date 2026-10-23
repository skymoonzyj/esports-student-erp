import { readPrivateJson } from './talentOwnedConfig'
import { fixedCapabilityDiagnostic } from './helpers/esportsCapabilityDiagnostic.mjs'

export function esportsOwnedConfig() {
  const file = process.env.FRONTEND_NAMED_CONFIG
  if (!file) return null
  const payload = readPrivateJson(file)
  if (payload.check !== 'esports-games') return null
  const url = new URL(payload.baseURL)
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port || url.pathname !== '/'
    || url.username || url.password || url.search || url.hash || !Number.isInteger(payload.entry)
    || payload.entry < 0 || payload.entry > 5 || (!['existing', 'audio'].includes(payload.mode) && !fixedCapabilityDiagnostic(payload))
    || (payload.mode === 'audio' && payload.entry !== 5)) throw new Error('Fixed owned esports entry required')
  return payload
}
