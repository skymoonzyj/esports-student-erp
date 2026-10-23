import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { ProcurementPage } from './ProcurementPage'

const loaded = vi.hoisted(() => ({ dialogs: 0 }))
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ status: 'authenticated', user: { account_id: 'actor' } }) }))
vi.mock('./ProcurementDialogs', () => { loaded.dialogs++; return {
  PurchaseFormDialog: ({ onClose }: { onClose: () => void }) => <button onClick={onClose}>close purchase</button>,
  InvoiceDialog: () => <p>invoice loaded</p>, SettlementDialog: () => null, ReimbursementDialog: () => null,
} })
vi.mock('./api', () => ({ listPurchases: async () => ({ items: [], total: 0, page: 1, page_size: 50 }), listSettlementCenters: async () => [] }))
it('procurement_list_does_not_load_dialogs', async () => {
  render(<QueryClientProvider client={new QueryClient()}><ProcurementPage canManage canSettle canExport canManageCenters canManageInvoices /></QueryClientProvider>)
  await screen.findByText('当前条件下没有采购记录。')
  expect(loaded.dialogs).toBe(0)
  fireEvent.click(screen.getByRole('button', { name: '新增采购' }))
  fireEvent.click(await screen.findByText('close purchase'))
  fireEvent.click(screen.getByRole('button', { name: '上传发票' }))
  expect(await screen.findByText('invoice loaded')).toBeVisible()
  expect(loaded.dialogs).toBe(1)
})
