import type { ControlSettings, RawResult, TaskConfig } from '../contracts'
import type {
  FpsCheckpointSnapshot,
  FpsSceneSnapshot,
  FpsTargetSnapshot,
  GameEngine,
  Size,
} from '../engineRuntime/types'
import { applyLook, INITIAL_CAMERA, nearestAngularHit, sampleVisibleDirection, targetPosition } from './math'
import { createRng, FpsTimeline, freezeResult, validateFpsSetup } from './schedule'

interface HorizontalPoint {
  readonly x: number
  readonly z: number
}

interface HitEvidence {
  readonly hit_latency_ms: number
  readonly hit_speed_m_s: number
  readonly pre_hit_moving_ms: number
}

interface RoundState {
  readonly index: number
  readonly target: FpsTargetSnapshot
  readonly checkpoint: Omit<FpsCheckpointSnapshot, 'state'>
  hit: HitEvidence | null
}

interface MoveFireV3Round {
  readonly hit: boolean
  readonly reached: boolean
  readonly hit_latency_ms: number | null
  readonly hit_speed_m_s: number | null
  readonly pre_hit_moving_ms: number | null
}

function horizontalDistance(a: HorizontalPoint, b: HorizontalPoint): number {
  return Math.hypot(a.x - b.x, a.z - b.z)
}

function isCheckpointReached(
  player: HorizontalPoint,
  checkpoint: HorizontalPoint,
  tolerance: number,
): boolean {
  const scale = Math.max(
    1,
    Math.abs(player.x),
    Math.abs(player.z),
    Math.abs(checkpoint.x),
    Math.abs(checkpoint.z),
    tolerance,
  )
  return horizontalDistance(player, checkpoint) <= tolerance + Number.EPSILON * scale * 16
}

function measurement(value: number): number {
  const rounded = Math.round(value * 1e12) / 1e12
  return Object.is(rounded, -0) ? 0 : rounded
}

function meetsMinimum(value: number, minimum: number): boolean {
  const scale = Math.max(1, Math.abs(value), Math.abs(minimum))
  return value + Number.EPSILON * scale * 16 >= minimum
}

export function createMoveFireV3Engine(
  config: TaskConfig,
  seed: number,
  settings: ControlSettings,
  viewport: Size,
): GameEngine {
  validateFpsSetup(config, seed, settings, viewport)
  const p = config.params
  if (config.suite_id !== 'mobile-fps'
    || config.test_id !== 'move-fire'
    || p.kind !== 'move_fire_v3') {
    throw new Error('mobile-fps move-fire v3 配置不匹配')
  }

  const rng = createRng(seed)
  let camera: FpsSceneSnapshot['camera'] = {
    ...INITIAL_CAMERA,
    position: { ...INITIAL_CAMERA.position },
  }
  let movement = { x: 0, y: 0 }
  let current: RoundState | null = null
  let qualifyingMs = 0
  let currentSpeed = 0
  let stationaryHit = false
  let shots = 0
  let finished: RawResult | undefined
  const rounds: MoveFireV3Round[] = []

  const createRound = (index: number): RoundState => {
    const offset = p.target_offset_min_deg
      + rng() * (p.target_offset_max_deg - p.target_offset_min_deg)
    const direction = sampleVisibleDirection(
      camera,
      offset,
      p.target_diameter_deg,
      viewport,
      rng,
      p.pitch_limit_deg,
    )
    const yaw = camera.yaw_deg * Math.PI / 180
    const side = rng() < .5 ? -1 : 1
    return {
      index,
      target: {
        id: `move-fire-v3-target-${index}`,
        position: targetPosition(direction, camera.position, 10),
        angular_diameter_deg: p.target_diameter_deg,
        type: 'neutral',
        state: 'highlighted',
      },
      checkpoint: {
        id: `move-fire-v3-checkpoint-${index}`,
        position: {
          x: camera.position.x + side * Math.cos(yaw) * p.checkpoint_distance_m,
          y: 0,
          z: camera.position.z + side * Math.sin(yaw) * p.checkpoint_distance_m,
        },
        radius: p.checkpoint_tolerance_m,
      },
      hit: null,
    }
  }

  const resetQualification = (): void => {
    qualifyingMs = 0
    currentSpeed = 0
    stationaryHit = false
  }

  const integrateMovement = (durationMs: number): void => {
    if (durationMs <= 0) return
    const before = camera.position
    const magnitude = Math.hypot(movement.x, movement.y)
    const scale = magnitude > 1 ? 1 / magnitude : 1
    const localRight = movement.x * scale
    const localForward = -movement.y * scale
    const yaw = camera.yaw_deg * Math.PI / 180
    const travelled = p.player_speed_m_s * durationMs / 1000
    const deltaX = travelled * (
      localRight * Math.cos(yaw) + localForward * Math.sin(yaw)
    )
    const deltaZ = travelled * (
      localRight * Math.sin(yaw) - localForward * Math.cos(yaw)
    )
    const position = {
      ...before,
      x: before.x + deltaX,
      z: before.z + deltaZ,
    }
    camera = { ...camera, position }

    const travelledMeters = travelled * Math.min(1, magnitude)
    currentSpeed = travelledMeters * 1000 / durationMs
    if (meetsMinimum(currentSpeed, p.min_moving_speed_m_s)) {
      qualifyingMs += durationMs
      stationaryHit = false
    } else {
      qualifyingMs = 0
    }
  }

  const finishRound = (): void => {
    if (!current) return
    rounds.push({
      hit: current.hit !== null,
      reached: isCheckpointReached(
        camera.position,
        current.checkpoint.position,
        p.checkpoint_tolerance_m,
      ),
      hit_latency_ms: current.hit?.hit_latency_ms ?? null,
      hit_speed_m_s: current.hit?.hit_speed_m_s ?? null,
      pre_hit_moving_ms: current.hit?.pre_hit_moving_ms ?? null,
    })
    const next = current.index + 1
    resetQualification()
    current = next < p.opportunity_count ? createRound(next) : null
  }

  current = createRound(0)
  const timeline = new FpsTimeline({
    onAdvance: (from, to) => {
      let cursor = from
      while (cursor < to - 1e-9) {
        const boundary = (rounds.length + 1) * p.window_ms
        const segmentEnd = Math.min(to, boundary)
        integrateMovement(segmentEnd - cursor)
        cursor = segmentEnd
        if (cursor >= boundary - 1e-9) finishRound()
      }
    },
    onInput: action => {
      if (action.kind === 'look') {
        camera = applyLook(camera, action.dx, action.dy, p.pitch_limit_deg)
      } else if (action.kind === 'move') {
        const magnitude = Math.min(1, Math.hypot(action.x, action.y))
        const commandedSpeed = magnitude * p.player_speed_m_s
        const wasQualifying = meetsMinimum(currentSpeed, p.min_moving_speed_m_s)
        movement = { x: action.x, y: action.y }
        if (!meetsMinimum(commandedSpeed, p.min_moving_speed_m_s)) {
          currentSpeed = commandedSpeed
          qualifyingMs = 0
        } else {
          currentSpeed = wasQualifying ? commandedSpeed : 0
        }
      }
    },
    onShot: at => {
      shots += 1
      if (!current || current.hit) return true
      const target = { ...current.target, state: 'highlighted' as const }
      if (!nearestAngularHit(camera, [target])) return true

      const eligible = meetsMinimum(currentSpeed, p.min_moving_speed_m_s)
        && meetsMinimum(qualifyingMs, p.min_moving_ms)
      if (!eligible) {
        stationaryHit = true
        return true
      }
      current.hit = {
        hit_latency_ms: measurement(at - current.index * p.window_ms),
        hit_speed_m_s: measurement(currentSpeed),
        pre_hit_moving_ms: measurement(qualifyingMs),
      }
      stationaryHit = false
      return true
    },
  }, p.fire_interval_ms)

  const scene = (): FpsSceneSnapshot => {
    const active = current
    return {
      kind: 'fps',
      at_ms: timeline.at_ms,
      camera: { ...camera, position: { ...camera.position } },
      targets: active
        ? [{
            ...active.target,
            position: { ...active.target.position },
            state: active.hit ? 'hit' as const : 'highlighted' as const,
          }]
        : [],
      checkpoints: active
        ? [{
            ...active.checkpoint,
            position: { ...active.checkpoint.position },
            state: isCheckpointReached(
              camera.position,
              active.checkpoint.position,
              p.checkpoint_tolerance_m,
            )
              ? 'reached' as const
              : 'active' as const,
          }]
        : [],
      movement_feedback: {
        state: active?.hit
          ? 'hit'
          : stationaryHit
            ? 'stationary-hit'
            : qualifyingMs > 0
              ? 'moving'
              : 'ready',
        qualifying_ms: active?.hit?.pre_hit_moving_ms ?? measurement(qualifyingMs),
      },
    }
  }

  return {
    input: action => { if (!finished) timeline.input(action) },
    advanceTo: elapsed_ms => { if (!finished) timeline.advanceTo(elapsed_ms) },
    scene,
    finish: () => {
      if (!finished) {
        timeline.advanceTo(60000)
        finished = freezeResult(config, { kind: 'move_fire_v3', rounds, shots })
      }
      return finished
    },
  }
}
