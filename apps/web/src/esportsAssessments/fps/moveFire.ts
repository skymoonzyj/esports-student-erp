import type { ControlSettings, RawResult, TaskConfig } from '../contracts'
import type {
  FpsCheckpointSnapshot,
  FpsSceneSnapshot,
  FpsTargetSnapshot,
  GameEngine,
  Size,
} from '../engineRuntime/types'
import { applyLook, INITIAL_CAMERA, nearestAngularHit, targetPosition } from './math'
import { createRng, FpsTimeline, freezeResult, validateFpsSetup } from './schedule'

interface HorizontalPoint {
  readonly x: number
  readonly z: number
}

interface RoundState {
  readonly index: number
  readonly target: FpsTargetSnapshot
  readonly checkpoint: Omit<FpsCheckpointSnapshot, 'state'>
  hit: boolean
}

export function distance(a: HorizontalPoint, b: HorizontalPoint): number {
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
  return distance(player, checkpoint) <= tolerance + Number.EPSILON * scale * 16
}

export function createMoveFireEngine(
  config: TaskConfig,
  seed: number,
  settings: ControlSettings,
  viewport: Size,
): GameEngine {
  validateFpsSetup(config, seed, settings, viewport)
  const p = config.params
  if (config.suite_id !== 'mobile-fps' || config.test_id !== 'move-fire' || p.kind !== 'move_fire') {
    throw new Error('mobile-fps move-fire 配置不匹配')
  }

  const rng = createRng(seed)
  let camera: FpsSceneSnapshot['camera'] = {
    ...INITIAL_CAMERA,
    position: { ...INITIAL_CAMERA.position },
  }
  let movement = { x: 0, y: 0 }
  let current: RoundState | null = null
  let shots = 0
  let finished: RawResult | undefined
  const rounds: Array<{ hit: boolean; reached: boolean }> = []

  const createRound = (index: number): RoundState => {
    const yaw = camera.yaw_deg
    const radians = yaw * Math.PI / 180
    const side = rng() < .5 ? -1 : 1
    return {
      index,
      target: {
        id: `move-fire-target-${index}`,
        position: targetPosition(
          { yaw_deg: yaw, pitch_deg: config.engine_version === 'mobile-fps-v2' ? camera.pitch_deg : 0 },
          { ...camera.position },
          10,
        ),
        angular_diameter_deg: p.target_diameter_deg,
        type: 'neutral',
        state: 'highlighted',
      },
      checkpoint: {
        id: `move-fire-checkpoint-${index}`,
        position: {
          x: camera.position.x + side * Math.cos(radians) * p.checkpoint_distance_m,
          y: 0,
          z: camera.position.z + side * Math.sin(radians) * p.checkpoint_distance_m,
        },
        radius: p.checkpoint_tolerance_m,
      },
      hit: false,
    }
  }

  const integrateMovement = (duration_ms: number): void => {
    const magnitude = Math.hypot(movement.x, movement.y)
    if (magnitude === 0 || duration_ms <= 0) return
    const scale = magnitude > 1 ? 1 / magnitude : 1
    const localRight = movement.x * scale
    const localForward = -movement.y * scale
    const yaw = camera.yaw_deg * Math.PI / 180
    const travelled = p.player_speed_m_s * duration_ms / 1000
    camera = {
      ...camera,
      position: {
        ...camera.position,
        x: camera.position.x + travelled * (
          localRight * Math.cos(yaw) + localForward * Math.sin(yaw)
        ),
        z: camera.position.z + travelled * (
          localRight * Math.sin(yaw) - localForward * Math.cos(yaw)
        ),
      },
    }
  }

  const finishRound = (): void => {
    if (!current) return
    rounds.push({
      hit: current.hit,
      reached: isCheckpointReached(
        camera.position,
        current.checkpoint.position,
        p.checkpoint_tolerance_m,
      ),
    })
    const next = current.index + 1
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
        movement = { x: action.x, y: action.y }
      }
    },
    onShot: () => {
      shots += 1
      if (!current || current.hit) return true
      const target = { ...current.target, state: 'highlighted' as const }
      if (nearestAngularHit(camera, [target])) current.hit = true
      return true
    },
  }, p.fire_interval_ms)

  const scene = (): FpsSceneSnapshot => {
    const active = current
    const target = active
      ? [{
          ...active.target,
          position: { ...active.target.position },
          state: active.hit ? 'hit' as const : 'highlighted' as const,
        }]
      : []
    const checkpoint = active
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
      : []
    return {
      kind: 'fps',
      at_ms: timeline.at_ms,
      camera: { ...camera, position: { ...camera.position } },
      targets: target,
      checkpoints: checkpoint,
    }
  }

  return {
    input: action => { if (!finished) timeline.input(action) },
    advanceTo: elapsed_ms => { if (!finished) timeline.advanceTo(elapsed_ms) },
    scene,
    finish: () => {
      if (!finished) {
        timeline.advanceTo(60000)
        finished = freezeResult(config, { kind: 'move_fire', rounds, shots })
      }
      return finished
    },
  }
}
