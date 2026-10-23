import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { AssistedProfileCorrectionPanel } from './AssistedProfileCorrectionPanel'

const loaded = vi.hoisted(() => ({ form: 0 }))
vi.mock('../auth/AuthProvider', () => ({
  useAuth: () => ({ status: 'authenticated', user: { account_id: 'actor' } }),
  useOptionalAuth: () => ({ status: 'authenticated', user: { account_id: 'actor' } }),
}))
vi.mock('./AssistedPersonForm', () => { loaded.form++; return { AssistedPersonForm: () => <input aria-label="draft" /> } })
vi.mock('./editorApi', () => ({
  useAssistedEditor: () => ({ context: { allowed_actions: ['correct_profile'], profile_version: 4, kind: 'student' }, blocked: false, reload: vi.fn() }),
  useEditorOperation: () => ({ version: 4 }), newEditorOperationKey: vi.fn(), assistedEditorApi: {},
}))
it('details_dont_load_correction_before_open', async () => {
  render(<QueryClientProvider client={new QueryClient()}><AssistedProfileCorrectionPanel personId="one" /></QueryClientProvider>)
  expect(loaded.form).toBe(0)
  fireEvent.click(screen.getByRole('button', { name: '更正本人及监护资料' }))
  expect(await screen.findByLabelText('draft')).toBeVisible()
  expect(loaded.form).toBe(1)
})
