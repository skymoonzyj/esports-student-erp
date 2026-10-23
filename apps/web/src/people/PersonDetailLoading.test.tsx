import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { PersonDetailPage } from './PersonDetailPage'
import { installModuleLoadEvents } from '../app/loading/moduleLoadEvents'

vi.mock('../auth/AuthProvider', () => ({
  useOptionalAuth: () => ({ status: 'authenticated', user: { account_id: 'actor' } }),
  useAuth: () => ({ status: 'authenticated', user: { account_id: 'actor', permissions: ['esports_assessments.read'], scoped_permissions: [] } }),
}))
vi.mock('../app/routeStyles', () => ({ loadRouteStyles: async () => {
  const error = new Error('Unable to preload CSS for /assets/reports.css')
  window.dispatchEvent(Object.assign(new Event('vite:preloadError'), { payload: error }))
  throw error
} }))
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })
it('keeps the person profile and reveal preview usable when only the report panel CSS fails', async () => {
  vi.stubGlobal('__ERP_BUILD_ID__', 'unknown')
  const uninstall = installModuleLoadEvents()
  // React reports caught boundary errors to the console; the UI below is the assertion.
  vi.spyOn(console, 'error').mockImplementation(() => undefined)
  const requests: string[] = []
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async input => {
    requests.push(String(input))
    return new Response(JSON.stringify({ id: 'person', full_name: '测试人员', nickname: '小宇', person_types: ['visitor'], identity_number: '110105********002X', phone: null, email: null, sex: 'female', date_of_birth: '2000-01-02', created_at: '2026-07-26T10:00:00+08:00', age_status: 'adult', guardian_status: 'not_applicable', student_number: null, student_status: null, staff_type: null, visit_reason: null, linked_student_id: null }), { headers: { 'Content-Type': 'application/json' } })
  }))
  try {
    render(<MemoryRouter initialEntries={['/people/person']}><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Routes><Route path="/people/:personId" element={<PersonDetailPage permissions={new Set(['people.read', 'people.identity.reveal', 'esports_assessments.read'])} />} /></Routes></QueryClientProvider></MemoryRouter>)
    expect(await screen.findByText('页面样式资源无法局部重试，请刷新页面。')).toBeVisible()
    expect(screen.getByText('昵称：小宇')).toBeVisible()
    expect(screen.getByRole('button', { name: '重试加载' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '查看完整身份证' }))
    expect(screen.getByRole('dialog')).toBeVisible()
    expect(requests.some(url => url.includes('/esports-assessments/'))).toBe(false)
  } finally { uninstall() }
})
