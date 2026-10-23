import { readPrivateJson } from './talentOwnedConfig'

export function assistedPrivateConfig() {
  const file = process.env.ASSISTED_PRIVATE_CONFIG
  if (!file) return null
  const payload = readPrivateJson(file)
  const url = new URL(payload.baseURL)
  if (!['assisted', 'team', 'registration', 'housing-occupancy', 'housing-reporting'].includes(payload.suite)
    || payload.noMedia !== true || url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port
    || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error('Fixed private functional suite required')
  return payload
}
