import type { ControlSettings, TaskConfig } from '../contracts'
import type { GameEngine, Size } from '../engineRuntime/types'
import { createPrecisionEngine } from './precision'
import { createSwitchingEngine } from './switching'
import { createSwitchingV3Engine } from './switchingV3'
import { createTrackingEngine } from './tracking'
import { createMultiTargetEngine } from './multiTarget'
import { createMultiTargetV5Engine } from './multiTargetV5'
import { createSpheretrack90Engine } from './spheretrack90'

export { createMultiTargetEngine } from './multiTarget'
export { createMultiTargetV5Engine } from './multiTargetV5'
export { createSpheretrack90Engine } from './spheretrack90'

export { validateFpsViewport } from './schedule'

export function createFpsEngine(config: TaskConfig, seed: number, settings: ControlSettings, viewport: Size): GameEngine {
  switch (config.test_id) {
    case 'gridshot': case 'sixshot': return config.engine_version === 'pc-fps-v5'
      ? createMultiTargetV5Engine(config, seed, settings, viewport)
      : createMultiTargetEngine(config, seed, settings, viewport)
    case 'spheretrack90': return createSpheretrack90Engine(config, seed, settings, viewport)
    case 'precision': return createPrecisionEngine(config, seed, settings, viewport)
    case 'tracking': return createTrackingEngine(config, seed, settings, viewport)
    case 'switching': return config.engine_version === `${config.suite_id}-v3`
      ? createSwitchingV3Engine(config, seed, settings, viewport)
      : createSwitchingEngine(config, seed, settings, viewport)
    default: throw new Error('未登记的公共 FPS 项目')
  }
}
