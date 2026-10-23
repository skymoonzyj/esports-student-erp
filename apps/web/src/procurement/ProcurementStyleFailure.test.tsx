import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ProcurementPage } from './ProcurementPage'
import { installModuleLoadEvents } from '../app/loading/moduleLoadEvents'

const loaded = vi.hoisted(() => ({ dialogs: 0 }))
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ status: 'authenticated', user: { account_id: 'actor' } }) }))
vi.mock('./ProcurementDialogs', () => { loaded.dialogs++; return {} })
vi.mock('../app/routeStyles', async () => {
  const { createStyleGate } = await import('../app/loading/styleGate')
  const gate = createStyleGate(async () => {
    const error = new Error('Unable to preload CSS for /assets/procurement-dialogs.css')
    window.dispatchEvent(Object.assign(new Event('vite:preloadError'), { payload: error }))
    throw error
  })
  return { loadRouteStyles: gate }
})
vi.mock('./api', () => ({ listPurchases: async () => ({ items: [], total: 0, page: 1, page_size: 50 }), listSettlementCenters: async () => [] }))
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })
it('blocks a distinct invoice consumer after purchase CSS fails without clearing parent filters', async () => {
  vi.stubGlobal('__ERP_BUILD_ID__', 'unknown')
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 404 })))
  vi.spyOn(console, 'error').mockImplementation(() => undefined)
  const uninstall = installModuleLoadEvents()
  try {
    render(<QueryClientProvider client={new QueryClient()}><ProcurementPage canManage canSettle canExport canManageCenters canManageInvoices /></QueryClientProvider>)
    await screen.findByText('当前条件下没有采购记录。')
    fireEvent.change(screen.getByLabelText('搜索物品'), { target: { value: 'retained parent draft' } })
    fireEvent.click(screen.getByRole('button', { name: '新增采购' }))
    await screen.findByText('页面样式资源无法局部重试，请刷新页面。')
    fireEvent.click(screen.getByRole('button', { name: '上传发票' }))
    await waitFor(() => expect(screen.getAllByText('页面样式资源无法局部重试，请刷新页面。')).toHaveLength(2))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(loaded.dialogs).toBe(0)
    expect(screen.getByLabelText('搜索物品')).toHaveValue('retained parent draft')
  } finally { uninstall() }
})
