import type { ControlSettings, RawResult, TaskConfig } from '../contracts'
import type { FpsSceneSnapshot, FpsTargetSnapshot, GameEngine, Size } from '../engineRuntime/types'
import { applyLook, directionOnCone, INITIAL_CAMERA, isTargetFullyVisible, nearestAngularHit, targetPosition } from './math'
import { createRng, FpsTimeline, freezeResult, validateFpsSetup } from './schedule'

export function createSwitchingEngine(config: TaskConfig, seed: number, settings: ControlSettings, viewport: Size): GameEngine {
  validateFpsSetup(config, seed, settings, viewport)
  const p = config.params
  if (config.test_id !== 'switching' || p.kind !== 'aim') throw new Error('switching 配置不匹配')
  const rng = createRng(seed)
  const currentView = config.engine_version === `${config.suite_id}-v2`
  let camera: FpsSceneSnapshot['camera'] = { ...INITIAL_CAMERA, position: { ...INITIAL_CAMERA.position } }
  const spawn = () => {
    const reference = currentView ? camera : INITIAL_CAMERA
    for (let attempt = 0; attempt <= 48; attempt++) {
      const bearing = attempt === 48 ? 0 : rng() * 2 * Math.PI
      const angles = [bearing, bearing + Math.PI].map(value => directionOnCone(reference, p.target_separation_deg / 2, value))
      if (angles.every(angle => (!currentView || Math.abs(angle.pitch_deg) <= p.pitch_limit_deg + 1e-10)
        && isTargetFullyVisible(angle, p.target_diameter_deg / 2, reference, viewport))) return angles.map(angle => targetPosition(angle, camera.position))
    }
    throw new Error('当前 viewport 视野不能显示固定切换靶组')
  }
  const pairs = currentView ? [spawn()] : Array.from({ length: p.group_count }, spawn)
  const trials = Array.from({ length: p.opportunity_count }, () => ({ hit: false, latency_ms: p.window_ms }))
  let shots = 0, wrong = 0, finished: RawResult | undefined
  const targetsAt = (at: number): FpsTargetSnapshot[] => {
    const group = Math.min(p.group_count - 1, Math.floor(at / p.group_ms))
    const current = Math.floor(at / p.window_ms)
    return pairs[group].map((position, offset) => {
      const index = group * 2 + offset
      return { id: `switching-${index}`, position: { ...position }, angular_diameter_deg: p.target_diameter_deg, type: 'neutral', state: at >= 60000 ? 'hidden' : trials[index].hit ? 'hit' : current === index ? 'highlighted' : 'active' }
    })
  }
  const timeline = new FpsTimeline({
    onAdvance: (_from, to) => {
      if (currentView) {
        const last = Math.min(p.group_count - 1, Math.floor(to / p.group_ms))
        while (pairs.length <= last) pairs.push(spawn())
      }
    },
    onInput: action => { if (action.kind === 'look') camera = applyLook(camera, action.dx, action.dy, p.pitch_limit_deg) },
    onShot: at => {
      shots++
      const hit = nearestAngularHit(camera, targetsAt(at))
      if (hit?.state === 'highlighted') {
        const index = Math.floor(at / p.window_ms)
        trials[index] = { hit: true, latency_ms: at - index * p.window_ms }
      } else if (hit) wrong++
      return true
    },
  }, p.fire_interval_ms)
  return {
    input: action => { if (!finished) timeline.input(action) },
    advanceTo: at => { if (!finished) timeline.advanceTo(at) },
    scene: () => ({ kind: 'fps', at_ms: timeline.at_ms, camera: { ...camera, position: { ...camera.position } }, targets: targetsAt(timeline.at_ms), checkpoints: [] }),
    finish: () => {
      if (!finished) { timeline.advanceTo(60000); finished = freezeResult(config, { kind: 'aim', trials, shots, wrong_target_shots: wrong }) }
      return finished
    },
  }
}
