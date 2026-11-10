export interface DamageTarget {
  id: string
  enemyIndex: number | null
  health: number
  hitAt: number | null
  diedAt: number | null
  windup: number | null
}
export interface DamageHit {
  sourceOrder: number
  order: number
  target: DamageTarget
  damage: number
  byPlayer: boolean
}
export interface ResolvedDeath { id: string; unit_id: number | null; by_player: boolean }

export function resolveDamage(hits: DamageHit[], at: number): ResolvedDeath[] {
  const deaths: ResolvedDeath[] = []
  for (const hit of [...hits].sort((a, b) => a.sourceOrder - b.sourceOrder || a.order - b.order)) {
    if (hit.target.health <= 0) continue
    hit.target.health = Math.max(0, hit.target.health - hit.damage)
    hit.target.hitAt = at
    if (!hit.target.health) {
      hit.target.diedAt = at
      hit.target.windup = null
      deaths.push({ id: hit.target.id, unit_id: hit.target.enemyIndex, by_player: hit.byPlayer })
    }
  }
  return deaths
}
