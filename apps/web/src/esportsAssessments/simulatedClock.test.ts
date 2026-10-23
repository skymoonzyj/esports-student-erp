import { afterEach, expect, it, vi } from 'vitest'
import type { Page } from '@playwright/test'
import { AssessmentTestClock } from '../../tests/helpers/esportsClock'

afterEach(() => { document.body.replaceChildren(); vi.restoreAllMocks() })

function harness() {
  let epoch = 1_000_000
  let phase: string | null = 'intro'
  let frozen = false
  let notifications = 0
  let afterAdvance: string | null = 'running'
  let disappearAfterCount = false
  const host = document.createElement('section')
  host.setAttribute('aria-label', '测试场景')
  host.setAttribute('data-host-phase', phase)
  document.body.append(host)
  function setPhase(value: string | null) {
    phase = value
    if (value === null) host.remove()
    else host.setAttribute('data-host-phase', value)
  }
  const page = {
    clock: {
      async install() {},
      async pauseAt(date: Date) {
        if (date.getTime() < epoch) throw new Error('clock cannot travel backwards')
        epoch = date.getTime(); frozen = true
      },
      async runFor(ms: number) { epoch += ms; setPhase(afterAdvance) },
      async resume() { frozen = false; notifications++ },
    },
    async evaluate(callback: () => unknown) {
      const clock = vi.spyOn(Date, 'now').mockReturnValue(epoch)
      try { return callback() } finally { clock.mockRestore() }
    },
    locator() { return {
      async getAttribute() { if (phase === null) throw new Error('detached Host locator timed out'); return phase },
      async count() { const count = phase === null ? 0 : 1; if (disappearAfterCount) setPhase(null); return count },
    } },
  } as unknown as Page
  return { page, clock: new AssessmentTestClock(), get frozen() { return frozen }, get notifications() { return notifications },
    phase: setPhase, afterAdvance(value: string | null) { afterAdvance = value },
    detachAfterCount() { disappearAfterCount = true } }
}

it('lets deferred report notifications run after the measured game ends', async () => {
  const h = harness()
  await h.clock.install(h.page); await h.clock.pauseBeforeTrial(h.page)
  h.afterAdvance('pending')
  await h.clock.advance(h.page, 60_500)
  expect(h.frozen).toBe(false)
  expect(h.notifications).toBe(1)
})

it.each(['trial', 'starting', 'countdown', 'running'])('never resumes the clock while %s is active', async phase => {
  const h = harness()
  await h.clock.install(h.page); await h.clock.pauseBeforeTrial(h.page)
  h.afterAdvance(phase)
  await h.clock.advance(h.page, 50)
  expect(h.frozen).toBe(true)
  expect(h.notifications).toBe(0)
})

it('can pause a second project after the virtual clock has moved beyond wall time', async () => {
  const h = harness()
  await h.clock.install(h.page); await h.clock.pauseBeforeTrial(h.page)
  h.afterAdvance('saved'); await h.clock.advance(h.page, 60_500)
  h.phase('intro'); await h.clock.pauseBeforeTrial(h.page)
  expect(h.frozen).toBe(true)
  h.afterAdvance('running'); await h.clock.advance(h.page, 3_100)
  expect(h.notifications).toBe(1)
})

it('resumes after report navigation has removed the finished Host', async () => {
  const h = harness()
  await h.clock.install(h.page); await h.clock.pauseBeforeTrial(h.page)
  h.afterAdvance(null); await h.clock.advance(h.page, 60_500)
  expect(h.frozen).toBe(false)
})

it('reads terminal Host state atomically when report navigation can detach it', async () => {
  const h = harness()
  await h.clock.install(h.page); await h.clock.pauseBeforeTrial(h.page)
  h.afterAdvance('pending'); h.detachAfterCount()
  await h.clock.advance(h.page, 60_500)
  expect(h.frozen).toBe(false)
})

it('rejects unprepared and invalid advances without changing time', async () => {
  const h = harness()
  await expect(h.clock.advance(h.page, 100)).rejects.toThrow()
  await h.clock.install(h.page); await h.clock.pauseBeforeTrial(h.page)
  await expect(h.clock.advance(h.page, -1)).rejects.toThrow()
  await expect(h.clock.advance(h.page, Number.NaN)).rejects.toThrow()
  expect(h.notifications).toBe(0)
})
