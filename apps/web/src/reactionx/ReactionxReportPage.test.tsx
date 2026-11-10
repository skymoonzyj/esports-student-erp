import { act, cleanup, screen, waitFor } from '@testing-library/react'
import { focusManager } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import { Routes, Route } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { ReactionxReportPage } from './ReactionxReportPage'
import { renderAssessmentPage, studentFixture } from '../esportsAssessments/testSupport/renderAssessmentPage'
import fixtures from '../../../../shared/reactionx/v1/fixtures.json'

afterEach(() => { cleanup(); focusManager.setFocused(undefined); vi.unstubAllGlobals() })
it('does not request rankings when no saved server receipt can be validated', async () => {
  const urls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async input => { urls.push(String(input)); return Response.json({ receipt: null }) }))
  renderAssessmentPage(<Routes><Route path='/reports/:resultId' element={<ReactionxReportPage />} /></Routes>, { initialEntries: ['/reports/11111111-1111-4111-8111-111111111111'], account: { ...studentFixture, scoped_permissions: ['talent_tests.read'] } })
  expect(await screen.findByRole('alert')).toBeVisible()
  expect(urls.some(url => url.endsWith('/ranking'))).toBe(false)
  expect(screen.queryByText(/第 0 名/)).not.toBeInTheDocument()
})
it('labels process interruption elapsed as a lower bound and reuses server ranking presentation', async () => {
  const id = '11111111-1111-4111-8111-111111111111'
  const data = fixtures.cases[0]
  vi.stubGlobal('fetch', vi.fn(async input => String(input).endsWith('/ranking') ? Response.json({ status: 'not_eligible', reason: 'practice_only', group_label: '自由训练', calculated_at: '2026-10-31T00:00:00Z', participant_count: 0, metrics: [{ key: 'hit_count', label: '命中数', unit: '次', better: 'higher', decimals: 0 }], current: null, best: null, top_three: [] }) : Response.json({ receipt: { run_id: data.manifest.run_id, result_id: id, digest: 'a'.repeat(64), saved_at: '2026-10-31T00:00:00.000Z' }, subject_person_id: data.manifest.subject_person_id, actor_account_id: data.manifest.actor_account_id, manifest: data.manifest, envelope: { ...data.result, end: { kind: 'process_interrupted', active_elapsed_ms: 200 } }, metrics: { ...data.expected, comparison_key: 'manual', dimensions: [] }, ranking_eligible: false, ranking_reason: 'practice_only' })))
  renderAssessmentPage(<Routes><Route path='/reports/:resultId' element={<ReactionxReportPage />} /></Routes>, { initialEntries: [`/reports/${id}`], account: { ...studentFixture, scoped_permissions: ['talent_tests.read'] } })
  expect(await screen.findByText(/最后观测时长下界/)).toHaveTextContent('200')
  expect(screen.getByText('命中数')).toBeVisible()
  expect(await screen.findByText('本次为练习成绩，不参与正式排名。')).toBeVisible()
  expect(screen.queryByText(/第 0 名/)).not.toBeInTheDocument()
})
it.each(['report', 'ranking'])('foreground %s denial clears report and ranking despite production focus defaults', async deniedEndpoint => {
  const id = '11111111-1111-4111-8111-111111111111', data = fixtures.cases[0]
  const saved = { receipt: { run_id: data.manifest.run_id, result_id: id, digest: 'a'.repeat(64), saved_at: '2026-10-31T00:00:00.000Z' }, subject_person_id: data.manifest.subject_person_id, actor_account_id: data.manifest.actor_account_id, manifest: data.manifest, envelope: data.result, metrics: { ...data.expected, comparison_key: 'manual', dimensions: [] }, ranking_eligible: false, ranking_reason: 'practice_only' }
  const ranking = { status: 'not_eligible', reason: 'practice_only', group_label: '合成私有报告组', calculated_at: '2026-10-31T00:00:00Z', participant_count: 0, metrics: [{ key: 'hit_count', label: '命中数', unit: '次', better: 'higher', decimals: 0 }], current: null, best: null, top_three: [] }
  let revoked = false
  const reads: string[] = []
  vi.stubGlobal('fetch', vi.fn(async input => {
    const endpoint = String(input).endsWith('/ranking') ? 'ranking' : 'report'; reads.push(endpoint)
    if (revoked && endpoint === deniedEndpoint) return Response.json({ detail: 'private-hidden' }, { status: 403 })
    return Response.json(endpoint === 'ranking' ? ranking : saved)
  }))
  const { queryClient } = renderAssessmentPage(<Routes><Route path='/reports/:resultId' element={<ReactionxReportPage />} /></Routes>, { initialEntries: [`/reports/${id}`], account: { ...studentFixture, scoped_permissions: ['talent_tests.read'] } })
  queryClient.setDefaultOptions({ queries: { retry: false, refetchOnWindowFocus: false } })
  expect(await screen.findByText('合成私有报告组')).toBeVisible()
  expect(screen.getByText('服务器保存时间：2026-10-31 08:00（中国时间）')).toBeVisible()
  revoked = true
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('无权'))
  await waitFor(() => expect(reads.filter(item => item === deniedEndpoint).length).toBeGreaterThanOrEqual(2))
  expect(screen.queryByText('合成私有报告组')).not.toBeInTheDocument()
  expect(screen.queryByText(/服务器保存时间/)).not.toBeInTheDocument()
  await waitFor(() => expect(JSON.stringify(queryClient.getQueryCache().getAll().map(query => query.state.data))).not.toContain(id))
  expect(screen.queryByText('private-hidden')).not.toBeInTheDocument()
  revoked = false
  await userEvent.click(screen.getByRole('button', { name: '重试读取报告' }))
  expect(await screen.findByText('合成私有报告组')).toBeVisible()
})
