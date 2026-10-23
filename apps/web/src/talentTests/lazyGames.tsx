import { createElement, Suspense, type ComponentType } from 'react'
import { createLazyModule } from '../app/loading/lazyModule'
import { ModuleBoundary } from '../app/loading/ModuleBoundary'
import type { DynamicVisionRule, GameProps, TestId } from './contracts'

function game<P extends object>(testId: TestId, loader: () => Promise<{ default: ComponentType<P> }>) {
  const moduleId = `talent-game-${testId}`
  // GameHost's route owner has already completed talentTests.css. Each game's
  // unique CSS travels with its own import; no controls mount before it resolves.
  const entry = createLazyModule(moduleId, loader)
  return function LazyGame(props: P) {
    return <ModuleBoundary moduleId={moduleId} retry={entry.retry}>
      <Suspense fallback={<p role="status">正在加载测试项目…</p>}>
        {createElement(entry.Component, props)}
      </Suspense>
    </ModuleBoundary>
  }
}

export const ReactionGame = game<GameProps<'reactiontime'>>('reactiontime', async () => ({ default: (await import('./games/reaction/ReactionGame')).ReactionGame }))
export const AimGame = game<GameProps<'aim'>>('aim', async () => ({ default: (await import('./games/aim/AimGame')).AimGame }))
export const SequenceGame = game<GameProps<'sequence'>>('sequence', async () => ({ default: (await import('./games/sequence/SequenceGame')).SequenceGame }))
export const NumberGame = game<GameProps<'number-memory'>>('number-memory', async () => ({ default: (await import('./games/number/NumberGame')).NumberGame }))
export const VerbalGame = game<GameProps<'verbal-memory'>>('verbal-memory', async () => ({ default: (await import('./games/verbal/VerbalGame')).VerbalGame }))
export const ChimpGame = game<GameProps<'chimp'>>('chimp', async () => ({ default: (await import('./games/chimp/ChimpGame')).ChimpGame }))
export const VisualGame = game<GameProps<'memory'>>('memory', async () => ({ default: (await import('./games/visual/VisualGame')).VisualGame }))
export const DynamicVisionGame = game<GameProps<'dynamic-vision'> & { rule?: DynamicVisionRule; onAttemptStarted?: () => void }>('dynamic-vision', async () => ({ default: (await import('./games/dynamicVision/DynamicVisionGame')).DynamicVisionGame }))

export const lazyGames = {
  reactiontime: ReactionGame, aim: AimGame, sequence: SequenceGame,
  'number-memory': NumberGame, 'verbal-memory': VerbalGame,
  chimp: ChimpGame, memory: VisualGame, 'dynamic-vision': DynamicVisionGame,
} satisfies { [K in TestId]: ComponentType<GameProps<K>> }
