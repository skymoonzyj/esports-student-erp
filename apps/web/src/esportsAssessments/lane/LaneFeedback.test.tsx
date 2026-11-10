import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { LaneFeedback } from './LaneFeedback'
import type { LaneSceneState } from './types'

const lane: LaneSceneState = { attack_mode: 'melee', units: [], projectiles: [], selected_target_id: null, move_destination: null, wave: 2, wave_count: 15, last_hits: 1, enemy_deaths: 4, missed: 3, hero_status: { health: 0, max_health: 1000, deaths: 1, respawn_at_ms: 15000 } }

it('shows actual v5 waves and health with a rounded-up respawn countdown while the match clock continues', () => {
  const view = render(<LaneFeedback lane={lane} elapsedMs={10001} remainingSeconds={290} />)
  const feedback = screen.getByLabelText('兵线实时反馈')
  expect(feedback).toHaveTextContent('波次 2 / 15')
  expect(feedback).toHaveTextContent('生命 0 / 1000')
  expect(feedback).toHaveTextContent('死亡 1')
  expect(feedback).toHaveTextContent('复活倒计时 5 秒')
  view.rerender(<LaneFeedback lane={lane} elapsedMs={14001} remainingSeconds={286} />)
  expect(feedback).toHaveTextContent('复活倒计时 1 秒')
  expect(feedback).toHaveTextContent('剩余 4:46')
  view.rerender(<LaneFeedback lane={{ ...lane, hero_status: { health: 1000, max_health: 1000, deaths: 1, respawn_at_ms: null } }} elapsedMs={15000} remainingSeconds={285} />)
  expect(feedback).toHaveTextContent('生命 1000 / 1000')
  expect(feedback).not.toHaveTextContent('复活倒计时')
})

it('retains the v4 ten-wave HUD without invented health or death metrics', () => {
  const old: LaneSceneState = { attack_mode: 'melee', units: [], projectiles: [], selected_target_id: null, move_destination: null, wave: 2, wave_count: 10, last_hits: 1, enemy_deaths: 4, missed: 3 }
  render(<LaneFeedback lane={old} elapsedMs={0} remainingSeconds={20} trial />)
  expect(screen.getByLabelText('兵线实时反馈')).toHaveTextContent('波次 2 / 10')
  expect(screen.getByLabelText('兵线实时反馈')).toHaveTextContent('试练 ≤20秒 · 剩余 0:20')
  expect(screen.getByLabelText('兵线实时反馈')).not.toHaveTextContent(/生命|死亡|复活/)
})

it('keeps all eight visible cells and compact trial copy across healthy, damaged, dead and respawned v5 scenes', () => {
  const view = render(<LaneFeedback lane={undefined} waveCount={15} remainingSeconds={20} trial />)
  const feedback = screen.getByLabelText('兵线实时反馈')
  expect(feedback.children).toHaveLength(8)
  expect(feedback.children[0]).toHaveTextContent('试练 ≤20秒 · 剩余 0:20')
  for (const hero_status of [
    { health: 820, max_health: 1000, deaths: 2, respawn_at_ms: null },
    { health: 0, max_health: 1000, deaths: 3, respawn_at_ms: 15000 },
    { health: 1000, max_health: 1000, deaths: 3, respawn_at_ms: null },
  ]) {
    view.rerender(<LaneFeedback lane={{ ...lane, hero_status }} elapsedMs={10000} waveCount={15} remainingSeconds={290} />)
    expect(feedback.children).toHaveLength(8)
    expect(feedback).toHaveTextContent(`生命 ${hero_status.health} / 1000`)
    expect(feedback).toHaveTextContent(`死亡 ${hero_status.deaths}`)
  }
})
