import { act, cleanup, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { ReactionxReportsPanel } from './ReactionxReportsPanel'
import { focusManager } from '@tanstack/react-query'
import { renderAssessmentPage, studentFixture } from '../esportsAssessments/testSupport/renderAssessmentPage'

afterEach(() => { cleanup(); focusManager.setFocused(undefined); vi.unstubAllGlobals() })
const person = studentFixture.person_id
it('uses only the minimal authorized results route with person filter', async () => {
  const urls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async input => { urls.push(String(input)); return Response.json({ items: [], next_cursor: null }) }))
  renderAssessmentPage(<ReactionxReportsPanel personId={person} />, { initialEntries: ['/'], account: { ...studentFixture, scoped_permissions: ['talent_tests.read'] } })
  expect(await screen.findByText('暂无已保存的实体灯报告')).toBeVisible()
  expect(urls).toEqual([`/api/v1/reactionx/results?limit=20&person_id=${person}`])
})
it('does not fetch for people-only reader, and isolates a private failure from the profile', async () => {
  const fetcher = vi.fn(async () => Response.json({ detail: 'SECRET' }, { status: 403 }))
  vi.stubGlobal('fetch', fetcher)
  const first = renderAssessmentPage(<ReactionxReportsPanel personId={person} />, { initialEntries: ['/'], account: { ...studentFixture, scoped_permissions: ['people.read'] } })
  expect(await screen.findByText('暂无实体灯报告读取权限')).toBeVisible()
  expect(fetcher).not.toHaveBeenCalled()
  first.unmount()
  renderAssessmentPage(<><h1>档案草稿</h1><ReactionxReportsPanel personId={person} /></>, { initialEntries: ['/'], account: { ...studentFixture, scoped_permissions: ['talent_tests.read'] } })
  expect(await screen.findByRole('alert')).toHaveTextContent('无权')
  expect(screen.getByRole('heading', { name: '档案草稿' })).toBeVisible()
  expect(screen.queryByText('SECRET')).not.toBeInTheDocument()
})
it('aborts previous person requests and ignores late private responses', async () => {
  let finish!: (response: Response) => void
  const pending = new Promise<Response>(resolve => { finish = resolve })
  let signal: AbortSignal | undefined | null
  vi.stubGlobal('fetch', vi.fn(async (input, init) => { if (String(input).includes(person)) { signal = init?.signal; return pending }; return Response.json({ items: [], next_cursor: null }) }))
  function Switch() { const [id, setId] = useState(person); return <><button onClick={() => setId('88888888-8888-4888-8888-888888888888')}>换人</button><ReactionxReportsPanel personId={id} /></> }
  renderAssessmentPage(<Switch />, { initialEntries: ['/'], account: { ...studentFixture, scoped_permissions: ['talent_tests.read'] } })
  await screen.findByText('正在读取已保存报告…')
  await userEvent.click(screen.getByRole('button', { name: '换人' }))
  expect(await screen.findByText('暂无已保存的实体灯报告')).toBeVisible()
  expect(signal?.aborted).toBe(true)
  await act(async () => { finish(Response.json({ items: [{ private: 'STALE' }], next_cursor: null })); await pending })
  expect(screen.queryByText('STALE')).not.toBeInTheDocument()
})
it('revalidates the report list on foreground under production no-focus defaults and clears private revoked data', async () => {
  let allowed = true, reads = 0
  const resultId = '11111111-1111-4111-8111-111111111111'
  vi.stubGlobal('fetch', vi.fn(async () => {
    reads++
    return allowed ? Response.json({ items: [{ receipt: { result_id: resultId, run_id: 'synthetic-run', digest: 'a'.repeat(64), saved_at: '2026-10-31T00:00:00.000Z' }, subject_person_id: person, actor_account_id: studentFixture.account_id, mode: 'manual', end_kind: 'user_stopped', metrics: { eligible: false, reason: 'practice_only', values: { hit_count: 9 }, comparison_key: 'manual', dimensions: [] }, ranking_eligible: false, ranking_reason: 'practice_only' }], next_cursor: null }) : Response.json({ detail: 'private-revoked-person' }, { status: 403 })
  }))
  const { queryClient } = renderAssessmentPage(<ReactionxReportsPanel personId={person} />, { initialEntries: ['/'], account: { ...studentFixture, scoped_permissions: ['talent_tests.read'] } })
  queryClient.setDefaultOptions({ queries: { retry: false, refetchOnWindowFocus: false } })
  expect(await screen.findByRole('link', { name: '查看实体灯报告' })).toBeVisible()
  allowed = false
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  expect(await screen.findByRole('alert')).toHaveTextContent('无权')
  expect(reads).toBe(2)
  expect(screen.queryByRole('link', { name: '查看实体灯报告' })).not.toBeInTheDocument()
  await waitFor(() => expect(JSON.stringify(queryClient.getQueryCache().getAll().map(query => query.state.data))).not.toContain(resultId))
  expect(screen.queryByText('private-revoked-person')).not.toBeInTheDocument()
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  expect(reads).toBe(2)
  allowed = true
  await userEvent.click(screen.getByRole('button', { name: '重试实体灯报告' }))
  expect(await screen.findByRole('link', { name: '查看实体灯报告' })).toBeVisible()
})
