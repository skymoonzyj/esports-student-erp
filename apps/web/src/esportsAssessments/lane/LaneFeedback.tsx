import type { LaneSceneState } from './types'

export function LaneFeedback({ lane, elapsedMs = 0, waveCount = 10, remainingSeconds, trial = false }: { lane: LaneSceneState | undefined; elapsedMs?: number; waveCount?: 10 | 15; remainingSeconds: number; trial?: boolean }) {
  const remaining = `${Math.floor(remainingSeconds / 60)}:${String(remainingSeconds % 60).padStart(2, '0')}`
  const hero = lane?.hero_status ?? (waveCount === 15 ? { health: 1000, max_health: 1000, deaths: 0, respawn_at_ms: null } : undefined)
  const respawnSeconds = hero?.respawn_at_ms === null || hero?.respawn_at_ms === undefined ? null : Math.max(0, Math.ceil((hero.respawn_at_ms - elapsedMs) / 1000))
  return <section className="assessmentMobaFeedback assessmentLaneFeedback" aria-label="兵线实时反馈">
    <span>{trial ? '试练 ≤20秒' : '正式五分钟'} · 剩余 {remaining}</span><span>波次 {lane?.wave ?? 0} / {lane?.wave_count ?? waveCount}</span>
    <span>补刀 {lane?.last_hits ?? 0}</span><span>漏刀 {lane?.missed ?? 0}</span>
    <span>补刀率 {lane?.enemy_deaths ? Math.round(lane.last_hits / lane.enemy_deaths * 100) : 0}%</span>
    {hero && <><span>生命 {hero.health} / {hero.max_health}</span><span>死亡 {hero.deaths}</span><span>{respawnSeconds !== null ? `复活倒计时 ${respawnSeconds} 秒` : '存活'}</span></>}
  </section>
}
