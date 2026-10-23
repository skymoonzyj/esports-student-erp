import type { GameFactory } from '../engineRuntime/types'
import { createDodgeEngine } from '../moba/dodge'
import { createDodgeChoiceEngine } from '../moba/dodgeChoice'
import { createMapEngine } from '../moba/mapAwareness'
import { createRhythmEngine } from '../moba/rhythm'
import { createSkillAimEngine } from '../moba/skillAim'

export const mobileMobaFactory: GameFactory = {
  create(config, seed, settings, viewport) {
    // Fixed logical MOBA geometry does not vary with the contained CSS viewport.
    void viewport
    if (config.suite_id !== 'mobile-moba') throw new Error('套系配置不匹配')
    switch (config.test_id) {
      case 'dodge': return config.engine_version === 'mobile-moba-v3' ? createDodgeChoiceEngine(config, seed, settings) : createDodgeEngine(config, seed, settings)
      case 'skill-aim': return createSkillAimEngine(config, seed, settings)
      case 'move-attack': return createRhythmEngine(config, seed, settings)
      case 'map-awareness': return createMapEngine(config, seed, settings)
      default: throw new Error('未登记的手机 MOBA 项目')
    }
  },
}
