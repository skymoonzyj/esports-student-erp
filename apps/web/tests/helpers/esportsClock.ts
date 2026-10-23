import type { Page } from '@playwright/test'

/** Test clocks must stay frozen only while explicitly advancing a game. */
export class AssessmentTestClock {
  private readonly simulated = new WeakSet<Page>()
  private readonly paused = new WeakSet<Page>()
  has(page: Page): boolean { return this.simulated.has(page) }
  async install(page: Page): Promise<void> {
    await page.clock.install({ time: new Date() })
    this.simulated.add(page)
  }
  async pauseBeforeTrial(page: Page): Promise<void> {
    if (!this.simulated.has(page) || this.paused.has(page)) return
    const phase = await page.locator('[aria-label="测试场景"]').getAttribute('data-host-phase')
    if (!['intro', 'preflight', 'interrupted'].includes(phase ?? '')) throw new Error('cannot jump an active test clock')
    // The page may already be minutes ahead after an earlier simulated item.
    const epoch = await page.evaluate(() => Date.now())
    await page.clock.pauseAt(new Date(epoch + 1_000))
    this.paused.add(page)
  }
  async advance(page: Page, milliseconds: number): Promise<void> {
    if (!this.simulated.has(page) || !this.paused.has(page) || !Number.isFinite(milliseconds) || milliseconds < 0) {
      throw new Error('simulated clock was not prepared before the run')
    }
    await page.clock.runFor(milliseconds)
    // Read once: navigation may remove the Host between separate locator calls.
    const phase = await page.evaluate(() => document.querySelector('[aria-label="测试场景"]')?.getAttribute('data-host-phase') ?? null)
    if (phase === null || ['pending', 'saved', 'interrupted'].includes(phase)) {
      // A finished game has no live clock to accelerate. Let actual async query
      // notifications and navigation run normally instead of stranding report UI.
      await page.clock.resume()
      this.paused.delete(page)
    }
  }
}
