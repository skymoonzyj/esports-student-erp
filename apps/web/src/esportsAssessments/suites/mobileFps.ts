import type { ControlRect, ControlSettings, TaskConfig } from '../contracts'
import type { GameFactory } from '../engineRuntime/types'
import { createFpsEngine } from '../fps'
import { createMoveFireEngine } from '../fps/moveFire'
import { createMoveFireV3Engine } from '../fps/moveFireV3'

function assertMobileFps(config: TaskConfig): void {
  if (config.suite_id !== 'mobile-fps') throw new Error('mobile-fps 套系配置不匹配')
}

export function activeMobileFpsControls(
  config: TaskConfig,
  settings: ControlSettings,
): readonly ControlRect[] {
  assertMobileFps(config)
  return settings.controls.filter(control => config.test_id === 'move-fire' || control.id !== 'move')
}

export const mobileFpsFactory: GameFactory = {
  create(config, seed, settings, viewport) {
    assertMobileFps(config)
    if (config.test_id === 'move-fire') {
      return config.params.kind === 'move_fire_v3'
        ? createMoveFireV3Engine(config, seed, settings, viewport)
        : createMoveFireEngine(config, seed, settings, viewport)
    }
    if (config.test_id === 'precision' || config.test_id === 'tracking' || config.test_id === 'switching') {
      return createFpsEngine(config, seed, settings, viewport)
    }
    throw new Error('mobile-fps 项目未登记')
  },
}
