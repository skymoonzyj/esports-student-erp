// @vitest-environment node
import { expect, it } from 'vitest'
import { expectLaneHudBounds, parseLaneLifeFeedback, type LaneHudBounds } from '../../tests/helpers/esportsAssessment'

it('reads only visible life feedback and retains nonzero baseline deaths after a real prior respawn', () => {
  expect(parseLaneLifeFeedback('生命 0 / 1000死亡 3复活倒计时 2 秒')).toEqual({ health: 0, max_health: 1000, deaths: 3, respawn_seconds: 2 })
  expect(parseLaneLifeFeedback('生命 1000 / 1000死亡 3存活')).toEqual({ health: 1000, max_health: 1000, deaths: 3, respawn_seconds: null })
  expect(parseLaneLifeFeedback('生命 820 / 1000死亡 4存活')).toEqual({ health: 820, max_health: 1000, deaths: 4, respawn_seconds: null })
  for (const text of ['生命 0 / 1000死亡 3存活', '生命 1000 / 1000死亡 3复活倒计时 2 秒', '生命 1001 / 1000死亡 3存活', 'private-account@example.test']) expect(() => parseLaneLifeFeedback(text)).toThrow(/visible lane life feedback/)
})

function layout(width: number): LaneHudBounds {
  const cellWidth = (width - 36) / 4
  return { viewport: { width: 1440, height: 900 }, feedback: { x: 100, y: 700, width, height: 64 }, cells: Array.from({ length: 8 }, (_, index) => {
    const x = 112 + index % 4 * (cellWidth + 4), y = 708 + Math.floor(index / 4) * 26
    return { box: { x, y, width: cellWidth, height: 22 }, text: { x, y: y + 2, width: 150, height: 18 } }
  }) }
}

it.each([800, 1016])('accepts all eight rendered cells/text within the fixed 64px feedback at width %s', width => {
  expect(() => expectLaneHudBounds(layout(width))).not.toThrow()
})

it('rejects the observed lower life-row clipping even when the outer feedback itself fits', () => {
  const clipped = layout(1016)
  clipped.cells[5] = { box: { x: 112, y: 752, width: 188, height: 22 }, text: { x: 112, y: 754, width: 150, height: 18 } }
  expect(() => expectLaneHudBounds(clipped)).toThrow()
})

it('rejects overflowing trial text even when its grid cell fits in the feedback', () => {
  const wrapped = layout(800)
  wrapped.cells[0].text.height = 44
  expect(() => expectLaneHudBounds(wrapped)).toThrow()
})
