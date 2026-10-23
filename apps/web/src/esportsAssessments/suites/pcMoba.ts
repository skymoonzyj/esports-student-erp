import type { GameFactory } from '../engineRuntime/types'
import { createLastHitEngine } from '../moba/lastHit'
import { createRhythmEngine } from '../moba/rhythm'
import { createDodgeEngine } from '../moba/dodge'
import { createDodgeChoiceEngine } from '../moba/dodgeChoice'
import { createMapEngine } from '../moba/mapAwareness'

export const pcMobaFactory: GameFactory = {
  create(config, seed, settings, viewport) {
    // Factory callers pass CSS viewport dimensions; MOBA physics stays logical.
    void viewport
    if (config.suite_id !== 'pc-moba') throw new Error('PC MOBA 套系配置不匹配')
    switch (config.test_id) {
      case 'last-hit': return createLastHitEngine(config, seed, settings)
      case 'orb-walk': return createRhythmEngine(config, seed, settings)
      case 'dodge': return config.engine_version === 'pc-moba-v3' ? createDodgeChoiceEngine(config, seed, settings) : createDodgeEngine(config, seed, settings)
      case 'map-awareness': return createMapEngine(config, seed, settings)
      default: throw new Error('未登记的 PC MOBA 项目')
    }
  },
}
