// @vitest-environment node
import { describe, expect, it, vi } from 'vitest'
import type { Page } from '@playwright/test'
import { activateOwnedDesktopBrowser, pressOwnedDesktopEscape } from '../../tests/helpers/desktopBrowserFocus'

const run = vi.hoisted(() => vi.fn())
vi.mock('node:child_process', () => ({ execFileSync: run }))

function fixture(processInfo: unknown = [{ type: 'browser', id: 4321 }]) {
  const send = vi.fn().mockResolvedValue({ processInfo })
  const detach = vi.fn().mockResolvedValue(undefined)
  const bringToFront = vi.fn().mockResolvedValue(undefined)
  const browser = { newBrowserCDPSession: vi.fn().mockResolvedValue({ send, detach }) }
  const press = vi.fn().mockResolvedValue(undefined)
  const page = { context: () => ({ browser: () => browser }), bringToFront, keyboard: { press } } as unknown as Page
  return { page, send, detach, bringToFront, browser, press }
}

describe('native desktop test window focus', () => {
  it.each([['linux', 'esports-desktop-1440'], ['darwin', 'esports-touch-844x390'], ['darwin', 'desktop-1440'], ['darwin', 'esports-webkit-capabilities']])('does not activate another platform or project: %s %s', async (platform, project) => {
    const f = fixture()
    run.mockClear()
    await activateOwnedDesktopBrowser(f.page, project, platform)
    expect(f.browser.newBrowserCDPSession).not.toHaveBeenCalled()
    expect(run).not.toHaveBeenCalled()
  })
  it('activates only the numeric browser PID obtained from its own connection', async () => {
    const f = fixture()
    run.mockReturnValue('true\n')
    await activateOwnedDesktopBrowser(f.page, 'esports-desktop-1440', 'darwin')
    expect(f.send).toHaveBeenCalledExactlyOnceWith('SystemInfo.getProcessInfo')
    expect(run).toHaveBeenCalledWith('/usr/bin/osascript', ['-l', 'JavaScript', '-e', expect.stringContaining('ProcessIdentifier(4321)')], expect.objectContaining({ timeout: 10000, stdio: ['ignore', 'pipe', 'pipe'] }))
    expect(f.detach).toHaveBeenCalledOnce()
    expect(f.bringToFront).toHaveBeenCalledOnce()
  })
  it.each([[{ type: 'renderer', id: 4321 }], [{ type: 'browser', id: '4321;unsafe' }], [{ type: 'browser', id: 0 }], [{ type: 'browser', id: 1 }, { type: 'browser', id: 2 }]])('rejects missing, malformed or ambiguous owned PID before OS action', async (...entries) => {
    const f = fixture(entries)
    run.mockClear()
    await expect(activateOwnedDesktopBrowser(f.page, 'esports-desktop-1440', 'darwin')).rejects.toThrow('无法确认本次测试浏览器进程')
    expect(run).not.toHaveBeenCalled()
    expect(f.detach).toHaveBeenCalledOnce()
  })
  it('does not swallow native activation refusal or leak OS output', async () => {
    const f = fixture()
    run.mockImplementation(() => { throw new Error('private OS detail') })
    await expect(activateOwnedDesktopBrowser(f.page, 'esports-desktop-1440', 'darwin')).rejects.toThrow('无法激活本次测试浏览器窗口')
    expect(f.detach).toHaveBeenCalledOnce()
  })
  it('posts native Escape down and up only to the previously activated owned browser', async () => {
    const f = fixture()
    run.mockReturnValue('true\n')
    await activateOwnedDesktopBrowser(f.page, 'esports-desktop-1440', 'darwin')
    run.mockClear()
    await pressOwnedDesktopEscape(f.page, 'esports-desktop-1440', 'darwin')
    expect(run).toHaveBeenCalledWith('/usr/bin/osascript', ['-l', 'JavaScript', '-e', expect.stringContaining('CGEventPostToPid(4321,down)')], expect.any(Object))
    expect(run.mock.calls[0][1][3]).toContain('CGEventPostToPid(4321,up)')
    expect(f.press).not.toHaveBeenCalled()
    expect(f.bringToFront).toHaveBeenCalledOnce()
  })
  it('refuses native Escape without established browser ownership', async () => {
    const f = fixture()
    run.mockClear()
    await expect(pressOwnedDesktopEscape(f.page, 'esports-desktop-1440', 'darwin')).rejects.toThrow('无法确认本次测试浏览器进程')
    expect(run).not.toHaveBeenCalled()
  })
  it('keeps ordinary Playwright keyboard behavior on other platforms', async () => {
    const f = fixture()
    run.mockClear()
    await pressOwnedDesktopEscape(f.page, 'esports-desktop-1440', 'linux')
    expect(f.press).toHaveBeenCalledExactlyOnceWith('Escape')
    expect(run).not.toHaveBeenCalled()
  })
})
