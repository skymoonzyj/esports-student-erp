import type { ControlSettings, RawResult, TaskConfig } from '../contracts'
import type { FpsSceneSnapshot, FpsTargetSnapshot, GameEngine, Size } from '../engineRuntime/types'
import { applyLook, INITIAL_CAMERA, nearestAngularHit, sampleVisibleDirection, targetPosition } from './math'
import { createRng, FpsTimeline, freezeResult, validateFpsSetup } from './schedule'

export function createSwitchingV3Engine(
  config: TaskConfig,
  seed: number,
  settings: ControlSettings,
  viewport: Size,
): GameEngine {
  validateFpsSetup(config, seed, settings, viewport)
  const p = config.params
  if (
    config.test_id !== 'switching'
    || config.engine_version !== `${config.suite_id}-v3`
    || p.kind !== 'aim'
  ) throw new Error('switching v3 配置不匹配')

  const rng = createRng(seed)
  const trials = Array.from(
    { length: p.opportunity_count },
    () => ({ hit: false, latency_ms: p.window_ms }),
  )
  let camera: FpsSceneSnapshot['camera'] = {
    ...INITIAL_CAMERA,
    position: { ...INITIAL_CAMERA.position },
  }
  let groupIndex = 0
  let firstHitAt: number | null = null
  let activeIndex: 0 | 1 | null = 0
  let target: FpsTargetSnapshot | null = null
  let shots = 0
  let finished: RawResult | undefined

  const spawn = (index: 0 | 1): FpsTargetSnapshot => {
    const distance = index === 0 ? p.target_separation_deg / 2 : p.target_separation_deg
    const direction = sampleVisibleDirection(
      camera,
      distance,
      p.target_diameter_deg,
      viewport,
      rng,
      p.pitch_limit_deg,
    )
    return {
      id: `switching-v3-${groupIndex * 2 + index}`,
      position: targetPosition(direction, camera.position),
      angular_diameter_deg: p.target_diameter_deg,
      type: 'neutral',
      state: 'highlighted',
    }
  }

  target = spawn(0)

  const timeline = new FpsTimeline({
    onAdvance: (_from, to) => {
      while (groupIndex + 1 < p.group_count && to >= (groupIndex + 1) * p.group_ms) {
        groupIndex++
        firstHitAt = null
        activeIndex = 0
        target = spawn(0)
      }

      if (to >= config.duration_ms) {
        activeIndex = null
        target = null
        return
      }

      const deadline = activeIndex === 0
        ? groupIndex * p.group_ms + p.window_ms
        : activeIndex === 1 && firstHitAt !== null
          ? firstHitAt + p.window_ms
          : null
      if (deadline !== null && to >= deadline) {
        activeIndex = null
        target = null
      }
    },
    onInput: action => {
      if (action.kind === 'look') camera = applyLook(camera, action.dx, action.dy, p.pitch_limit_deg)
    },
    onShot: at => {
      shots++
      if (activeIndex === null || target === null) return true
      if (!nearestAngularHit(camera, [target])) return true

      const hitIndex = activeIndex
      const trialIndex = groupIndex * 2 + hitIndex
      const origin = hitIndex === 0 ? groupIndex * p.group_ms : firstHitAt!
      trials[trialIndex] = { hit: true, latency_ms: at - origin }

      if (hitIndex === 0) {
        firstHitAt = at
        activeIndex = 1
        target = spawn(1)
      } else {
        activeIndex = null
        target = null
      }
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
      targets: target ? [{ ...target, position: { ...target.position } }] : [],
      checkpoints: [],
    }),
    finish: () => {
      if (!finished) {
        timeline.advanceTo(config.duration_ms)
        finished = freezeResult(config, {
          kind: 'aim',
          trials,
          shots,
          wrong_target_shots: 0,
        })
      }
      return finished
    },
  }
}
