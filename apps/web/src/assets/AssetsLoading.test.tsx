import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { AssetsPage } from './AssetsPage'

const loaded = vi.hoisted(() => ({ qr: 0 }))
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ status: 'authenticated', user: { account_id: 'actor' } }) }))
vi.mock('./AssetQrDialog', () => { loaded.qr++; return { AssetQrDialog: ({ onClose }: { onClose: () => void }) => <button onClick={onClose}>close QR</button> } })
vi.mock('./api', () => ({ listAssets: async () => ({ items: [{ id: 'one', name: 'Test asset', asset_code: 'AST-1', status: 'available' }], total: 1 }) }))

it('asset_list_does_not_load_qr_before_open', async () => {
  render(<QueryClientProvider client={new QueryClient()}><AssetsPage canManage={false} canAssign={false} /></QueryClientProvider>)
  await screen.findByText('Test asset')
  expect(loaded.qr).toBe(0)
  fireEvent.change(screen.getByLabelText('搜索资产'), { target: { value: 'parent filter draft' } })
  fireEvent.click(screen.getByRole('button', { name: '二维码' }))
  fireEvent.click(await screen.findByText('close QR'))
  expect(loaded.qr).toBe(1)
  expect(screen.getByLabelText('搜索资产')).toHaveValue('parent filter draft')
})
