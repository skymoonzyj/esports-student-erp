import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import { MobaInstructions } from './MobaInstructions'
import { fixtureMobaV4Config } from './testSupport/fixturesMobaV4'
import { fixtureMobaV5Config } from './testSupport/fixturesMobaV5'

it.each(['melee', 'ranged'] as const)('explains v5 %s towers, 20-second waves, 75 opportunities and continuing death time', mode => {
  render(<MobaInstructions config={fixtureMobaV5Config(mode)} />)
  const guide = screen.getByLabelText('本项测试说明')
  expect(guide).toHaveTextContent('每 20 秒')
  expect(guide).toHaveTextContent('75 个敌兵')
  expect(guide).toHaveTextContent('双方各两座防御塔')
  expect(guide).toHaveTextContent('5 秒后')
  expect(guide).toHaveTextContent('计时不停')
  expect(guide).toHaveTextContent('左键')
})

it('keeps historical v4 instructions at 50 opportunities without promising tower combat', () => {
  render(<MobaInstructions config={fixtureMobaV4Config()} />)
  const guide = screen.getByLabelText('本项测试说明')
  expect(guide).toHaveTextContent('50 个敌兵')
  expect(guide).not.toHaveTextContent(/防御塔|复活|75 个/)
})
