import { execFileSync } from 'node:child_process'
import type { Page } from '@playwright/test'

const activatedPages = new WeakSet<Page>()
async function ownedBrowserPid(page: Page): Promise<number> {
  const browser = page.context().browser()
  if (!browser) throw new Error('无法确认本次测试浏览器进程')
  const cdp = await browser.newBrowserCDPSession()
  try {
    const response = await cdp.send('SystemInfo.getProcessInfo')
    const candidates = response.processInfo.filter(item => item.type === 'browser')
    if (candidates.length !== 1 || !Number.isSafeInteger(candidates[0].id) || candidates[0].id <= 0) {
      throw new Error('无法确认本次测试浏览器进程')
    }
    return candidates[0].id
  } finally { await cdp.detach() }
}

function nativeAction(script: string, failure: string): void {
  try {
    const output = execFileSync('/usr/bin/osascript', ['-l', 'JavaScript', '-e', script],
      { encoding: 'utf8', timeout: 10000, stdio: ['ignore', 'pipe', 'pipe'],
        env: Object.fromEntries(['PATH', 'HOME', 'TMPDIR', 'LANG'].flatMap(key => process.env[key] ? [[key, process.env[key]]] : [])),
      })
    if (output.trim() !== 'true') throw new Error('native action refused')
  } catch { throw new Error(failure) }
}

/** macOS requires native window focus for Pointer Lock; DOM focus alone is insufficient.
 * Activate only the browser owned by this test connection, before any measured run.
 * Never emulate a successful lock or re-focus an interrupted assessment.
 */
export async function activateOwnedDesktopBrowser(page: Page, project: string, platform: string = process.platform): Promise<void> {
  if (platform !== 'darwin' || project !== 'esports-desktop-1440') return
  const pid = await ownedBrowserPid(page)
  await page.bringToFront()
  nativeAction(`ObjC.import('AppKit'); var app=$.NSRunningApplication.runningApplicationWithProcessIdentifier(${pid}); Boolean(app && app.activateWithOptions(3));`, '无法激活本次测试浏览器窗口')
  activatedPages.add(page)
}

export async function pressOwnedDesktopEscape(page: Page, project: string, platform: string = process.platform): Promise<void> {
  if (platform !== 'darwin' || project !== 'esports-desktop-1440') { await page.keyboard.press('Escape'); return }
  if (!activatedPages.has(page)) throw new Error('无法确认本次测试浏览器进程')
  const pid = await ownedBrowserPid(page)
  // CDP key events do not trigger the macOS browser's native Escape accelerator.
  // Deliver a real key pair to this owned PID, never to the global foreground app.
  nativeAction(`ObjC.import('CoreGraphics'); var down=$.CGEventCreateKeyboardEvent(null,53,true); var up=$.CGEventCreateKeyboardEvent(null,53,false); $.CGEventPostToPid(${pid},down); $.CGEventPostToPid(${pid},up); true;`, '无法向本次测试浏览器发送原生退出键')
}
