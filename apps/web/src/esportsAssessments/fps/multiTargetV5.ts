import type { ControlSettings, RawResult, TaskConfig } from '../contracts'
import type { FpsSceneSnapshot, FpsTargetSnapshot, GameEngine, Size } from '../engineRuntime/types'
import { ClickFpsTimeline } from './clickTimeline'
import { applyFixedGridLook, createFixedGridLayout } from './fixedGrid'
import { angularDistanceDeg, INITIAL_CAMERA, nearestAngularHit, targetPosition, vectorToDirection, type AngleDirection } from './math'
import { createRng, freezeResult, validateFpsSetup } from './schedule'

/** Independent fixed-world successor; historical multiTarget stays unchanged. */
export function createMultiTargetV5Engine(config: TaskConfig, seed: number, settings: ControlSettings, viewport: Size): GameEngine {
  validateFpsSetup(config, seed, settings, viewport)
  const p = config.params
  if (config.engine_version !== 'pc-fps-v5' || config.suite_id !== 'pc-fps' || !['gridshot', 'sixshot'].includes(config.test_id) || p.kind !== 'multi_target') throw new Error('pc-fps-v5 multi_target 配置不匹配')
  const { candidates: fixedCandidates, bounds } = createFixedGridLayout(p, viewport)
  const rng = createRng(seed)
  let camera: FpsSceneSnapshot['camera'] = { ...INITIAL_CAMERA, position: { ...INITIAL_CAMERA.position } }
  const targets: FpsTargetSnapshot[] = []
  const shots: { at_ms: number; hit: boolean }[] = []
  let serial = 0, finished: RawResult | undefined
  const angleOf = (target: FpsTargetSnapshot) => vectorToDirection({ x: target.position.x - INITIAL_CAMERA.position.x, y: target.position.y - INITIAL_CAMERA.position.y, z: target.position.z - INITIAL_CAMERA.position.z })
  const spawn = (excluded?: AngleDirection, retained: readonly FpsTargetSnapshot[] = targets): FpsTargetSnapshot => {
    const candidates = fixedCandidates.filter(candidate =>
      (!excluded || Math.abs(candidate.yaw_deg - excluded.yaw_deg) > 1e-8 || Math.abs(candidate.pitch_deg - excluded.pitch_deg) > 1e-8)
      && retained.every(target => angularDistanceDeg(candidate, angleOf(target)) >= p.target_diameter_deg - 1e-8))
    if (!candidates.length) throw new Error('固定靶区没有可完整显示且不重叠的登记网格位置')
    const candidate = candidates[Math.floor(rng() * candidates.length)]
    return { id: `${config.test_id}-${serial++}`, position: targetPosition(candidate, INITIAL_CAMERA.position), angular_diameter_deg: p.target_diameter_deg, type: 'neutral', state: 'highlighted', shape: 'sphere' }
  }
  while (targets.length < p.target_count) targets.push(spawn())
  const timeline = new ClickFpsTimeline(at_ms => {
    const hit = nearestAngularHit(camera, targets)
    if (hit) {
      const index = targets.findIndex(target => target.id === hit.id)
      const replacement = spawn(angleOf(hit), targets.filter(target => target.id !== hit.id))
      targets[index] = replacement
    }
    shots.push({ at_ms, hit: !!hit })
  }, action => {
    if (action.kind === 'look') camera = applyFixedGridLook(camera, action.dx, action.dy, bounds)
  })
  return {
    input: action => { if (!finished) timeline.input(action) },
    advanceTo: at => { if (!finished) timeline.advanceTo(at) },
    scene: () => ({ kind: 'fps', at_ms: timeline.at_ms, camera: { ...camera, position: { ...camera.position } }, targets: targets.map(target => ({ ...target, position: { ...target.position }, state: timeline.at_ms >= 60000 ? 'hidden' : target.state })), checkpoints: [], feedback: { kind: 'multi_target', shots: shots.length, hits: shots.filter(shot => shot.hit).length } }),
    finish: () => {
      if (!finished) { timeline.advanceTo(60000); finished = freezeResult(config, { kind: 'multi_target', shots }) }
      return finished
    },
  }
}
