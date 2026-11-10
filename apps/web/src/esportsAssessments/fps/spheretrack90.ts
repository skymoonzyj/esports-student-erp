import type { ControlSettings, RawResult, TaskConfig } from '../contracts'
import type { FpsSceneSnapshot, GameEngine, Size } from '../engineRuntime/types'
import { angularDistanceDeg, applyLook, INITIAL_CAMERA, targetPosition, type AngleDirection } from './math'
import { createRng, FpsTimeline, freezeResult, validateFpsSetup } from './schedule'

const STEP = 1000 / 120
const delta = (from: number, to: number) => Math.atan2(Math.sin(to - from), Math.cos(to - from))

/** Independent v4 bounded trajectory. The 90° label describes its FOV, not a turn angle. */
export function createSpheretrack90Engine(config: TaskConfig, seed: number, settings: ControlSettings, viewport: Size): GameEngine {
  validateFpsSetup(config, seed, settings, viewport)
  const p = config.params
  if (config.suite_id !== 'pc-fps' || config.test_id !== 'spheretrack90' || p.kind !== 'spheretrack90') throw new Error('spheretrack90 配置不匹配')
  const rng = createRng(seed)
  const directionTicks = Math.round(p.direction_interval_ms * 120 / 1000)
  let x = 10, y = 0, heading = rng() * Math.PI * 2, turnFrom = heading, turnTo = heading
  const path: AngleDirection[] = [{ yaw_deg: x, pitch_deg: y }]
  for (let tick = 1; tick <= 7200; tick++) {
    if (tick > 1 && (tick - 1) % directionTicks === 0) {
      turnFrom = heading
      turnTo = rng() * Math.PI * 2
    }
    heading = turnFrom + delta(turnFrom, turnTo) * Math.min(1, (((tick - 1) % directionTicks) * STEP + STEP / 2) / p.turn_ms)
    x += Math.cos(heading) * p.speed_deg_s / 120
    y += Math.sin(heading) * p.speed_deg_s / 120
    if (Math.abs(x) > p.horizontal_bound_deg) {
      x = Math.sign(x) * (2 * p.horizontal_bound_deg - Math.abs(x))
      heading = Math.PI - heading; turnFrom = Math.PI - turnFrom; turnTo = Math.PI - turnTo
    }
    if (Math.abs(y) > p.vertical_bound_deg) {
      y = Math.sign(y) * (2 * p.vertical_bound_deg - Math.abs(y))
      heading = -heading; turnFrom = -turnFrom; turnTo = -turnTo
    }
    path.push({ yaw_deg: x, pitch_deg: y })
  }
  const positionAt = (at: number): AngleDirection => {
    const tick = Math.min(7200, at / STEP), index = Math.floor(tick), fraction = tick - index
    const a = path[index], b = path[Math.min(7200, index + 1)]
    return { yaw_deg: a.yaw_deg + (b.yaw_deg - a.yaw_deg) * fraction, pitch_deg: a.pitch_deg + (b.pitch_deg - a.pitch_deg) * fraction }
  }
  let camera: FpsSceneSnapshot['camera'] = { ...INITIAL_CAMERA, position: { ...INITIAL_CAMERA.position } }
  let coverage = 0, losses = 0, covered = false, lostAt: number | null = null, lastLossBucket = -1, finished: RawResult | undefined
  const reacquisition: number[] = []
  const onTarget = (at: number) => angularDistanceDeg(camera, positionAt(at)) <= p.target_diameter_deg / 2
  const timeline = new FpsTimeline({
    onInput: action => { if (action.kind === 'look') camera = applyLook(camera, action.dx, action.dy, p.pitch_limit_deg) },
    onAdvance: (from, to) => {
      const nowCovered = onTarget((from + to) / 2)
      if (nowCovered) {
        coverage += to - from
        if (!covered && lostAt !== null) { reacquisition.push(from - lostAt); lostAt = null }
      } else if (covered) {
        const bucket = Math.floor(from * 120 / 1000 + 1e-7)
        if (bucket !== lastLossBucket) { losses++; lostAt = from; lastLossBucket = bucket }
      }
      covered = nowCovered
    },
  })
  const floor = (value: number) => Math.floor(value * 1e6) / 1e6
  return {
    input: action => { if (!finished && action.kind === 'look') timeline.input(action) },
    advanceTo: at => { if (!finished) timeline.advanceTo(at) },
    scene: () => ({ kind: 'fps', at_ms: timeline.at_ms, camera: { ...camera, position: { ...camera.position } }, targets: [{ id: 'spheretrack90', position: targetPosition(positionAt(timeline.at_ms)), angular_diameter_deg: p.target_diameter_deg, type: 'neutral', state: timeline.at_ms >= 60000 ? 'hidden' : 'highlighted', shape: 'sphere' }], checkpoints: [], feedback: { kind: 'spheretrack90', covered: onTarget(timeline.at_ms) } }),
    finish: () => {
      if (!finished) {
        timeline.advanceTo(60000)
        finished = freezeResult(config, { kind: 'spheretrack90', coverage_ms: floor(coverage), loss_count: losses, reacquisition_ms: reacquisition.map(floor) })
      }
      return finished
    },
  }
}
