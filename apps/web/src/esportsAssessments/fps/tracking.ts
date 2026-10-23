import type { ControlSettings, RawResult, TaskConfig } from '../contracts'
import type { FpsSceneSnapshot, GameEngine, Size } from '../engineRuntime/types'
import { angularDistanceDeg, applyLook, INITIAL_CAMERA, targetPosition, type AngleDirection } from './math'
import { createRng, FpsTimeline, freezeResult, validateFpsSetup } from './schedule'

function angleDelta(from: number, to: number): number { return Math.atan2(Math.sin(to - from), Math.cos(to - from)) }

export function createTrackingEngine(config: TaskConfig, seed: number, settings: ControlSettings, viewport: Size): GameEngine {
  validateFpsSetup(config, seed, settings, viewport)
  const p = config.params
  if (config.test_id !== 'tracking' || p.kind !== 'tracking') throw new Error('tracking 配置不匹配')
  const rng = createRng(seed)
  let x = 10, y = 0, heading = rng() * Math.PI * 2, turnFrom = heading, turnTo = heading
  const positions: AngleDirection[] = [{ yaw_deg: x, pitch_deg: y }]
  for (let tick = 1; tick <= 7200; tick++) {
    const from = (tick - 1) * 1000 / 120
    if (tick > 1 && (tick - 1) % (p.direction_interval_ms * 120 / 1000) === 0) {
      turnFrom = heading
      turnTo = rng() * Math.PI * 2
    }
    const phase = (from % p.direction_interval_ms) + 1000 / 240
    heading = turnFrom + angleDelta(turnFrom, turnTo) * Math.min(1, phase / p.turn_ms)
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
    positions.push({ yaw_deg: x, pitch_deg: y })
  }
  const positionAt = (at: number): AngleDirection => {
    const tick = Math.min(7200, at * 120 / 1000), index = Math.floor(tick)
    const a = positions[index], b = positions[Math.min(7200, index + 1)], fraction = tick - index
    return { yaw_deg: a.yaw_deg + (b.yaw_deg - a.yaw_deg) * fraction, pitch_deg: a.pitch_deg + (b.pitch_deg - a.pitch_deg) * fraction }
  }
  let camera: FpsSceneSnapshot['camera'] = { ...INITIAL_CAMERA, position: { ...INITIAL_CAMERA.position } }
  let coverage = 0, losses = 0, covered = false, lostAt: number | null = null, finished: RawResult | undefined
  let lastLossBucket = -1
  const reacquisition: number[] = []
  const timeline = new FpsTimeline({
    // Keep the default input-time integration: the old camera is integrated
    // through the event timestamp before onInput changes it. Coverage must
    // retain every sub-tick on/off interval, independently of counter sampling.
    onInput: action => { if (action.kind === 'look') camera = applyLook(camera, action.dx, action.dy, p.pitch_limit_deg) },
    onAdvance: (from, to) => {
      const nowCovered = angularDistanceDeg(camera, positionAt((from + to) / 2)) <= p.target_diameter_deg / 2
      if (nowCovered) {
        coverage += to - from
        if (!covered && lostAt !== null) { reacquisition.push(from - lostAt); lostAt = null }
      } else if (covered) {
        // Count at most the first actual loss in each 120Hz bucket. Its first
        // real reacquisition closes this exact, non-overlapping off-target
        // interval. Additional same-bucket losses still affect coverage, but
        // do not create counter entries; an ongoing loss never counts again.
        const bucket = Math.floor(from * 120 / 1000 + 1e-7)
        if (bucket !== lastLossBucket) {
          losses++
          lostAt = from
          lastLossBucket = bucket
        }
      }
      covered = nowCovered
    },
  })
  return {
    input: action => { if (!finished && action.kind === 'look') timeline.input(action) },
    advanceTo: at => { if (!finished) timeline.advanceTo(at) },
    scene: () => ({ kind: 'fps', at_ms: timeline.at_ms, camera: { ...camera, position: { ...camera.position } }, targets: [{ id: 'tracking', position: targetPosition(positionAt(timeline.at_ms)), angular_diameter_deg: p.target_diameter_deg, type: 'neutral', state: timeline.at_ms >= 60000 ? 'hidden' : 'highlighted' }], checkpoints: [] }),
    finish: () => {
      if (!finished) {
        timeline.advanceTo(60000)
        // Round down sub-microsecond floating error so disjoint measured
        // durations cannot sum above the registered 60000ms raw boundary.
        finished = freezeResult(config, { kind: 'tracking', coverage_ms: Math.floor(coverage * 1e6) / 1e6, loss_count: losses, reacquisition_ms: reacquisition.map(x => Math.floor(x * 1e6) / 1e6) })
      }
      return finished
    },
  }
}
