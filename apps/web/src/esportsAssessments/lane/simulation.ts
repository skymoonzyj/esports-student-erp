import type { AttackMode, LaneLastHitRaw, LaneTowerLastHitParams, LaneTowerLastHitRaw } from '../contracts'
import type { InputAction, Vector2 } from '../engineRuntime/types'
import type { LaneSceneState, LaneTowerSnapshot, LaneUnitSnapshot } from './types'
import { CREEP_STATS, DEATH_VISIBLE_MS, DURATION_MS, HERO_STATS, LANE_SIZE, PICK_RADIUS, SEPARATION_RADIUS, STEP_MS, WAVE_COUNT, WAVE_INTERVAL_MS, type CombatStats } from './constants'
import { resolveDamage } from './combat'

interface Unit {
  id: string
  order: number
  enemyIndex: number | null
  team: 'ally' | 'enemy'
  kind: 'hero' | 'melee' | 'ranged'
  position: { x: number; y: number }
  stats: CombatStats
  health: number
  facing: number
  target: string | null
  windup: number | null
  readyAt: number
  releasedAt: number | null
  hitAt: number | null
  diedAt: number | null
  moving: boolean
}
interface Missile {
  id: string
  order: number
  sourceOrder: number
  team: 'ally' | 'enemy'
  targetId: string
  position: { x: number; y: number }
  speed: number
  damage: number
  byPlayer: boolean
  kind?: 'tower'
  targetLife?: number
}
interface Tower {
  id: string
  team: Unit['team']
  position: Vector2
  order: number
  facing: number
  target: string | null
  windup: number | null
  readyAt: number
  releasedAt: number | null
}
interface Hit { sourceOrder: number; order: number; target: Unit; damage: number; byPlayer: boolean }
const distance = (a: Vector2, b: Vector2) => Math.hypot(a.x - b.x, a.y - b.y)
const bound = (value: number, max: number, radius = 0) => Math.max(radius, Math.min(max - radius, value))

/** Fixed-clock simulation, independent from rendering, RAF cadence and wall time. */
export class LaneSimulation {
  at_ms = 0
  private tick = 0
  private wave = 0
  private units: Unit[] = []
  private missiles: Missile[] = []
  private missileSequence = 0
  private destination: Vector2 | null = null
  private readonly result: LaneLastHitRaw | LaneTowerLastHitRaw
  private readonly hero: Unit
  private readonly towers: Tower[]
  private heroLife = 0
  private respawnAt: number | null = null

  constructor(readonly mode: AttackMode, seed: number, private readonly towerParams?: LaneTowerLastHitParams) {
    // This ruleset intentionally has no random stat or formation variance.
    void seed
    this.result = towerParams
      ? { kind: 'lane_last_hit_towers', deaths: [], attacks: 0, cancelled_attacks: 0, hero_death_times: [], hero_damage_taken: 0 }
      : { kind: 'lane_last_hit', deaths: [], attacks: 0, cancelled_attacks: 0 }
    this.hero = this.unit('hero', 0, 'ally', 'hero', { x: towerParams ? 180 : 400, y: 420 },
      towerParams ? { ...HERO_STATS[mode], health: towerParams.hero_health } : HERO_STATS[mode], null)
    this.towers = towerParams?.tower_positions.map((tower, index) => ({
      id: tower.id, team: tower.team, position: { x: tower.x, y: tower.y }, order: 10000 + index,
      facing: tower.team === 'ally' ? 0 : Math.PI, target: null, windup: null, readyAt: 0, releasedAt: null,
    })) ?? []
    this.units.push(this.hero)
    this.spawnWave()
  }

  private unit(id: string, order: number, team: Unit['team'], kind: Unit['kind'], position: Vector2, stats: CombatStats, enemyIndex: number | null): Unit {
    return { id, order, team, kind, position: { ...position }, stats, enemyIndex, health: stats.health,
      facing: team === 'ally' ? 0 : Math.PI, target: null, windup: null, readyAt: 0, releasedAt: null,
      hitAt: null, diedAt: null, moving: false }
  }

  private spawnWave(): void {
    const wave = this.wave++
    for (const team of ['ally', 'enemy'] as const) {
      for (let slot = 0; slot < 5; slot++) {
        const kind = slot < 3 ? 'melee' : 'ranged'
        const x = kind === 'melee' ? 100 : 60
        const y = [247.25, 281.25, 315.25, 264.25, 298.25][slot]
        this.units.push(this.unit(`${team}-${wave}-${slot}`, 1 + wave * 10 + (team === 'ally' ? 0 : 5) + slot,
          team, kind, { x: team === 'ally' ? x : LANE_SIZE.width - x, y }, CREEP_STATS[kind], team === 'enemy' ? wave * 5 + slot : null))
      }
    }
  }

  advanceTo(at: number): void {
    if (!Number.isFinite(at) || at < this.at_ms) return
    this.at_ms = Math.min(at, DURATION_MS)
    // The endpoint is exclusive for every combat/spawn event, not just waves.
    while (this.tick + STEP_MS <= this.at_ms && this.tick + STEP_MS < DURATION_MS) {
      this.tick += STEP_MS
      this.step()
      // New units are shown at their exact spawn coordinates until next tick.
      if (this.tick % (this.towerParams?.wave_interval_ms ?? WAVE_INTERVAL_MS) === 0 && this.wave < (this.towerParams?.wave_count ?? WAVE_COUNT)) this.spawnWave()
    }
  }

  input(action: InputAction): void {
    if (action.kind !== 'point' || !Number.isFinite(action.at_ms) || action.at_ms < this.at_ms || action.at_ms < 0 || action.at_ms >= DURATION_MS || !Number.isFinite(action.x) || !Number.isFinite(action.y)) return
    this.advanceTo(action.at_ms)
    if (this.hero.health <= 0) return
    const point = { x: bound(action.x, LANE_SIZE.width, 12), y: bound(action.y, LANE_SIZE.height, 12) }
    const liveEnemies = this.units.filter(unit => unit.team === 'enemy' && unit.health > 0)
    const target = action.target_id !== undefined
      ? liveEnemies.find(unit => unit.id === action.target_id)
      : liveEnemies.filter(unit => distance(unit.position, point) <= PICK_RADIUS)
      .sort((a, b) => distance(a.position, point) - distance(b.position, point) || a.order - b.order)[0]
    // A visible-frame identity can die before this input tick. Ignore that stale
    // command rather than silently attacking another unit under the ground point.
    if (typeof action.target_id === 'string' && !target) return
    if (target?.id === this.hero.target) return
    if (this.hero.windup !== null) this.result.cancelled_attacks++
    this.hero.windup = null
    this.hero.target = target?.id ?? null
    this.destination = target ? null : point
  }

  private move(unit: Unit, target: Vector2, stopAt = 0): void {
    const d = distance(unit.position, target)
    const travel = Math.min(unit.stats.speed * STEP_MS / 1000, Math.max(0, d - stopAt))
    if (!travel || !d) return
    unit.facing = Math.atan2(target.y - unit.position.y, target.x - unit.position.x)
    unit.position.x = bound(unit.position.x + (target.x - unit.position.x) / d * travel, LANE_SIZE.width, unit.kind === 'hero' ? 12 : SEPARATION_RADIUS)
    unit.position.y = bound(unit.position.y + (target.y - unit.position.y) / d * travel, LANE_SIZE.height, unit.kind === 'hero' ? 12 : SEPARATION_RADIUS)
    unit.moving = true
  }

  private step(): void {
    if (this.respawnAt !== null && this.tick >= this.respawnAt) {
      this.heroLife++
      Object.assign(this.hero, { health: this.hero.stats.health, position: { x: 180, y: 420 }, diedAt: null,
        hitAt: null, target: null, windup: null, readyAt: this.tick, releasedAt: null, moving: false, facing: 0 })
      this.respawnAt = null
    }
    this.units = this.units.filter(unit => unit === this.hero || unit.diedAt === null || this.tick - unit.diedAt <= DEATH_VISIBLE_MS)
    const byId = new Map(this.units.map(unit => [unit.id, unit]))
    const alive = this.units.filter(unit => unit.health > 0)
    const hits: Hit[] = []
    this.missiles = this.missiles.filter(missile => {
      const target = byId.get(missile.targetId)
      if (!target || target.health <= 0 || target.team === missile.team || (missile.targetLife !== undefined && missile.targetLife !== this.heroLife)) return false
      const d = distance(missile.position, target.position), travel = missile.speed * STEP_MS / 1000
      if (d <= travel) {
        hits.push({ ...missile, target })
        return false
      }
      missile.position.x += (target.position.x - missile.position.x) / d * travel
      missile.position.y += (target.position.y - missile.position.y) / d * travel
      return true
    })
    for (const unit of alive) {
      unit.moving = false
      let target = unit.target ? byId.get(unit.target) : undefined
      if (target && target.health <= 0) target = undefined
      if (unit.kind !== 'hero' && !target) {
        target = alive.filter(other => other.kind !== 'hero' && other.team !== unit.team)
          .sort((a, b) => distance(unit.position, a.position) - distance(unit.position, b.position) || a.order - b.order)[0]
      }
      unit.target = target?.id ?? null
      if (!target) {
        unit.windup = null
        if (unit.kind === 'hero') {
          if (this.destination) {
            this.move(unit, this.destination)
            if (distance(unit.position, this.destination) < 0.001) this.destination = null
          }
        } else this.move(unit, { x: unit.team === 'ally' ? 986 : 14, y: unit.position.y })
        continue
      }
      unit.facing = Math.atan2(target.position.y - unit.position.y, target.position.x - unit.position.x)
      if (unit.windup !== null) {
        if (this.tick - unit.windup >= unit.stats.windup) {
          unit.readyAt = unit.windup + unit.stats.interval
          unit.windup = null
          unit.releasedAt = this.tick
          if (unit.kind === 'hero') this.result.attacks++
          if (unit.stats.projectileSpeed) {
            const order = this.missileSequence++
            this.missiles.push({ id: `projectile-${order}`, order, sourceOrder: unit.order, team: unit.team,
              targetId: target.id, position: { ...unit.position }, speed: unit.stats.projectileSpeed, damage: unit.stats.damage, byPlayer: unit.kind === 'hero' })
          } else if (distance(unit.position, target.position) <= unit.stats.range + 1) {
            hits.push({ sourceOrder: unit.order, order: -1, target, damage: unit.stats.damage, byPlayer: unit.kind === 'hero' })
          }
        }
      } else if (distance(unit.position, target.position) > unit.stats.range + 0.001) {
        this.move(unit, target.position, unit.stats.range)
        // Reaching range is sufficient this tick. Waiting until the next tick
        // lets a fleeing target move away again and starves pursuit attacks.
        if (this.tick >= unit.readyAt && distance(unit.position, target.position) <= unit.stats.range + 0.001) unit.windup = this.tick
      } else if (this.tick >= unit.readyAt) unit.windup = this.tick
    }
    this.stepTowers(alive, byId)
    const heroHealthBefore = this.hero.health
    // Stable source/entity order also settles same-tick overkill exactly once.
    for (const death of resolveDamage(hits, this.tick)) {
      if (death.unit_id !== null) this.result.deaths.push({ unit_id: death.unit_id, at_ms: this.tick, by_player: death.by_player })
      for (const unit of alive) if (unit.target === death.id) {
        unit.target = null
        unit.windup = null
      }
    }
    if (this.result.kind === 'lane_last_hit_towers') {
      this.result.hero_damage_taken += heroHealthBefore - this.hero.health
      if (heroHealthBefore > 0 && this.hero.health === 0) {
        this.result.hero_death_times.push(this.tick)
        this.respawnAt = this.tick + this.towerParams!.respawn_ms
        this.hero.target = null
        this.hero.windup = null
        this.hero.moving = false
        this.destination = null
      }
    }
    this.separate(alive.filter(unit => unit.kind !== 'hero' && unit.health > 0))
  }

  private stepTowers(alive: Unit[], byId: Map<string, Unit>): void {
    const params = this.towerParams
    if (!params) return
    for (const tower of this.towers) {
      const legal = (unit: Unit) => unit.health > 0 && unit.team !== tower.team && distance(tower.position, unit.position) <= params.tower_range
      let target = tower.target ? byId.get(tower.target) : undefined
      if (target && !legal(target)) target = undefined
      if (!target) {
        tower.windup = null
        target = alive.filter(legal).sort((a, b) => Number(a.kind === 'hero') - Number(b.kind === 'hero') ||
          distance(tower.position, a.position) - distance(tower.position, b.position) || a.order - b.order)[0]
      }
      tower.target = target?.id ?? null
      if (!target) continue
      tower.facing = Math.atan2(target.position.y - tower.position.y, target.position.x - tower.position.x)
      if (tower.windup !== null) {
        if (this.tick - tower.windup >= params.tower_windup_ms) {
          tower.readyAt = tower.windup + params.tower_interval_ms
          tower.windup = null
          tower.releasedAt = this.tick
          const order = this.missileSequence++
          this.missiles.push({ id: `projectile-${order}`, order, sourceOrder: tower.order, team: tower.team,
            targetId: target.id, position: { ...tower.position }, speed: params.tower_projectile_speed,
            damage: params.tower_damage, byPlayer: false, kind: 'tower',
            ...(target.kind === 'hero' ? { targetLife: this.heroLife } : {}) })
        }
      } else if (this.tick >= tower.readyAt) tower.windup = this.tick
    }
  }

  private separate(units: Unit[]): void {
    // Ordered pair relaxation, no random jitter; the hero cannot body-block.
    for (let pass = 0; pass < 2; pass++) for (let i = 0; i < units.length; i++) for (let j = i + 1; j < units.length; j++) {
      const a = units[i], b = units[j], d = distance(a.position, b.position)
      if (d >= SEPARATION_RADIUS * 2) continue
      const dx = d ? (b.position.x - a.position.x) / d : 0
      const dy = d ? (b.position.y - a.position.y) / d : 1
      const correction = (SEPARATION_RADIUS * 2 - d) / 2
      for (const [unit, direction] of [[a, -1], [b, 1]] as const) {
        unit.position.x = bound(unit.position.x + direction * dx * correction, LANE_SIZE.width, SEPARATION_RADIUS)
        unit.position.y = bound(unit.position.y + direction * dy * correction, LANE_SIZE.height, SEPARATION_RADIUS)
      }
    }
  }

  snapshot(): LaneSceneState {
    const lastHits = this.result.deaths.filter(death => death.by_player).length
    return { attack_mode: this.mode, units: this.units.map(unit => this.unitSnapshot(unit)),
      projectiles: this.missiles.flatMap(missile => {
        const target = this.units.find(unit => unit.id === missile.targetId && unit.health > 0)
        return target && (missile.targetLife === undefined || missile.targetLife === this.heroLife)
          ? [{ id: missile.id, team: missile.team, position: { ...missile.position }, target: { ...target.position }, ...(missile.kind ? { kind: missile.kind } : {}) }] : []
      }), selected_target_id: this.hero.target, move_destination: this.destination ? { ...this.destination } : null,
      wave: this.wave, wave_count: this.towerParams ? 15 : 10, last_hits: lastHits, enemy_deaths: this.result.deaths.length, missed: this.result.deaths.length - lastHits,
      ...(this.result.kind === 'lane_last_hit_towers' ? {
        towers: this.towers.map((tower): LaneTowerSnapshot => ({ id: tower.id, team: tower.team, position: { ...tower.position },
          facing_rad: tower.facing, state: tower.windup !== null ? 'windup' : tower.releasedAt !== null && this.tick - tower.releasedAt < 180 ? 'attacking' : 'idle',
          attack_progress: tower.windup === null ? 0 : Math.min(1, (this.tick - tower.windup) / this.towerParams!.tower_windup_ms), target_id: tower.target })),
        hero_status: { health: this.hero.health, max_health: this.hero.stats.health, deaths: this.result.hero_death_times.length, respawn_at_ms: this.respawnAt },
      } : {}) }
  }

  private unitSnapshot(unit: Unit): LaneUnitSnapshot {
    return { id: unit.id, team: unit.team, kind: unit.kind, position: { ...unit.position }, facing_rad: unit.facing,
      health: unit.health, max_health: unit.stats.health, hit_at_ms: unit.hitAt,
      state: unit.health <= 0 ? 'dying' : unit.windup !== null ? 'windup' : unit.moving ? 'moving' : unit.releasedAt !== null && this.tick - unit.releasedAt < 180 ? 'attacking' : 'idle',
      attack_progress: unit.windup === null ? 0 : Math.min(1, (this.tick - unit.windup) / unit.stats.windup) }
  }

  raw(): LaneLastHitRaw | LaneTowerLastHitRaw {
    const deaths = this.result.deaths.map(death => ({ ...death })).sort((a, b) => a.at_ms - b.at_ms || a.unit_id - b.unit_id)
    return this.result.kind === 'lane_last_hit_towers'
      ? { ...this.result, deaths, hero_death_times: [...this.result.hero_death_times] }
      : { ...this.result, deaths }
  }
}
