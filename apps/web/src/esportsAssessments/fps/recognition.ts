import { recognitionSchedule, type ControlSettings, type RawResult, type TaskConfig } from '../contracts'
import type { FpsSceneSnapshot, FpsTargetSnapshot, GameEngine, Size } from '../engineRuntime/types'
import { applyLook, INITIAL_CAMERA, nearestAngularHit, sampleVisibleDirection, targetPosition } from './math'
import { createDistanceSchedule, createRng, FpsTimeline, freezeResult, validateFpsSetup } from './schedule'
import { usesCurrentFpsView } from '../versionRules'

export function createRecognitionEngine(
  config: TaskConfig,
  seed: number,
  settings: ControlSettings,
  viewport: Size,
): GameEngine {
  validateFpsSetup(config, seed, settings, viewport)
  const p = config.params
  if (config.test_id !== 'recognition' || p.kind !== 'recognition') throw new Error('recognition 配置不匹配')

  const kinds = recognitionSchedule(seed)
  const rng = createRng(seed)
  const currentView = usesCurrentFpsView(config)
  let camera: FpsSceneSnapshot['camera'] = { ...INITIAL_CAMERA, position: { ...INITIAL_CAMERA.position } }
  const distances = createDistanceSchedule(p.angle_distances_deg, p.distance_repetitions, seed)
  const spawn = (index: number) => targetPosition(
    sampleVisibleDirection(currentView ? camera : INITIAL_CAMERA, distances[index], p.target_diameter_deg, viewport, rng, p.pitch_limit_deg),
    camera.position,
  )
  const positions = currentView ? [spawn(0)] : distances.map((_distance, index) => spawn(index))
  const trials = kinds.map(target_kind => ({ target_kind, shot: false, hit: false, latency_ms: p.window_ms }))
  let finished: RawResult | undefined

  const targetsAt = (at: number): FpsTargetSnapshot[] => {
    if (at >= config.duration_ms) return []
    const index = Math.floor(at / p.round_ms)
    const elapsed = at - index * p.round_ms
    if (index >= trials.length || elapsed >= p.window_ms || trials[index].shot) return []
    return [{
      id: `recognition-${index}`,
      position: { ...positions[index] },
      angular_diameter_deg: p.target_diameter_deg,
      type: kinds[index],
      state: 'highlighted',
    }]
  }

  const timeline = new FpsTimeline({
    onAdvance: (_from, to) => {
      if (currentView) {
        const last = Math.min(distances.length - 1, Math.floor(to / p.round_ms))
        while (positions.length <= last) positions.push(spawn(positions.length))
      }
    },
    onInput: action => {
      if (action.kind === 'look') camera = applyLook(camera, action.dx, action.dy, p.pitch_limit_deg)
    },
    onShot: at => {
      const index = Math.floor(at / p.round_ms)
      const elapsed = at - index * p.round_ms
      if (index >= trials.length || elapsed >= p.window_ms || trials[index].shot) return false
      const hit = kinds[index] === 'enemy' && nearestAngularHit(camera, targetsAt(at)) !== null
      trials[index] = { target_kind: kinds[index], shot: true, hit, latency_ms: elapsed }
      return true
    },
  }, p.fire_interval_ms)

  return {
    input: action => { if (!finished) timeline.input(action) },
    advanceTo: at => { if (!finished) timeline.advanceTo(at) },
    scene: () => ({
      kind: 'fps',
      at_ms: timeline.at_ms,
      camera: { ...camera, position: { ...camera.position } },
      targets: targetsAt(timeline.at_ms),
      checkpoints: [],
    }),
    finish: () => {
      if (!finished) {
        timeline.advanceTo(config.duration_ms)
        finished = freezeResult(config, { kind: 'recognition', trials })
      }
      return finished
    },
  }
}
