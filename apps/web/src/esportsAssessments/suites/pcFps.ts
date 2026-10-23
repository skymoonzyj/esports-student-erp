import type { GameFactory } from '../engineRuntime/types'
import { createFpsEngine } from '../fps'
import { createRecognitionEngine } from '../fps/recognition'

export const pcFpsInstructions = Object.freeze([
  '移动鼠标瞄准，按鼠标左键射击。',
  '按 ESC 退出指针锁定；退出后本次测试将中断。',
  '敌我识别同时使用形状与文字提示，请勿只凭颜色判断。',
])

export const pcFpsFactory: GameFactory = {
  create(config, seed, settings, viewport) {
    if (config.suite_id !== 'pc-fps') throw new Error('套系配置不匹配')
    switch (config.test_id) {
      case 'recognition': return createRecognitionEngine(config, seed, settings, viewport)
      case 'precision':
      case 'tracking':
      case 'switching': return createFpsEngine(config, seed, settings, viewport)
      default: throw new Error('未登记的 PC FPS 项目')
    }
  },
}
