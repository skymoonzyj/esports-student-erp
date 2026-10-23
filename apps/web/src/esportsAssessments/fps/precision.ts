import type { ControlSettings, RawResult, TaskConfig } from '../contracts'
import type { FpsSceneSnapshot, FpsTargetSnapshot, GameEngine, Size } from '../engineRuntime/types'
import { applyLook, INITIAL_CAMERA, nearestAngularHit, sampleVisibleDirection, targetPosition } from './math'
import { createDistanceSchedule, createRng, FpsTimeline, freezeResult, validateFpsSetup } from './schedule'
import { usesCurrentFpsView } from '../versionRules'

export function createPrecisionEngine(config: TaskConfig, seed: number, settings: ControlSettings, viewport: Size): GameEngine {
  validateFpsSetup(config, seed, settings, viewport)
  const p = config.params
  if (config.test_id !== 'precision' || p.kind !== 'aim') throw new Error('precision 配置不匹配')
  const rng = createRng(seed)
  const currentView = usesCurrentFpsView(config)
  let camera: FpsSceneSnapshot['camera'] = { ...INITIAL_CAMERA, position: { ...INITIAL_CAMERA.position } }
  let reference = { yaw_deg: 0, pitch_deg: 0 }
  const distances = createDistanceSchedule(p.angle_distances_deg, p.distance_repetitions, seed)
  const spawn = (index: number) => {
    // V1 is frozen for existing attempts. V2 samples the camera only at the
    // opportunity boundary; looking afterward must not move the target.
    reference = sampleVisibleDirection(currentView ? camera : reference, distances[index], p.target_diameter_deg, viewport, rng, p.pitch_limit_deg)
    return targetPosition(reference, camera.position)
  }
  const positions = currentView ? [spawn(0)] : distances.map((_distance, index) => spawn(index))
  const trials = distances.map(() => ({ hit: false, latency_ms: p.window_ms }))
  let shots = 0, finished: RawResult | undefined
  const targetsAt = (at: number): FpsTargetSnapshot[] => {
    const index = Math.min(distances.length - 1, Math.floor(at / p.window_ms))
    return [{ id: `precision-${index}`, position: { ...positions[index] }, angular_diameter_deg: p.target_diameter_deg, type: 'neutral', state: at >= 60000 ? 'hidden' : trials[index].hit ? 'hit' : 'highlighted' }]
  }
  const timeline = new FpsTimeline({
    onAdvance: (_from, to) => {
      if (currentView) {
        const last = Math.min(distances.length - 1, Math.floor(to / p.window_ms))
        while (positions.length <= last) positions.push(spawn(positions.length))
      }
    },
    onInput: action => { if (action.kind === 'look') camera = applyLook(camera, action.dx, action.dy, p.pitch_limit_deg) },
    onShot: at => {
      shots++
      const index = Math.floor(at / p.window_ms)
      if (nearestAngularHit(camera, targetsAt(at))) trials[index] = { hit: true, latency_ms: at - index * p.window_ms }
      return true
    },
  }, p.fire_interval_ms)
  return {
    input: action => { if (!finished) timeline.input(action) },
    advanceTo: at => { if (!finished) timeline.advanceTo(at) },
    scene: () => ({ kind: 'fps', at_ms: timeline.at_ms, camera: { ...camera, position: { ...camera.position } }, targets: targetsAt(timeline.at_ms), checkpoints: [] }),
    finish: () => {
      if (!finished) { timeline.advanceTo(60000); finished = freezeResult(config, { kind: 'aim', trials, shots, wrong_target_shots: 0 }) }
      return finished
    },
  }
}
