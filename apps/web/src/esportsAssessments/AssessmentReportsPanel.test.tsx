import { act, cleanup, screen } from '@testing-library/react'
import { useState } from 'react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { AssessmentReportsPanel } from './AssessmentReportsPanel'
import { renderAssessmentPage, studentFixture } from './testSupport/renderAssessmentPage'
import { jsonResponse, privateResponse, summaryFixture, PERSON_ID, REPORT_ID } from './reportTestFixtures'

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
it('requests only authorized completed reports for the exact person and links into independent routes', async () => {
  const requests: string[] = []
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async input => { requests.push(String(input)); return jsonResponse({ items: [summaryFixture()], next_cursor: null, visible_total: 1 }) }))
  renderAssessmentPage(<AssessmentReportsPanel personId={PERSON_ID} />, { initialEntries: ['/people/' + PERSON_ID], account: studentFixture })
  expect(await screen.findByRole('link', { name: /查看报告/ })).toHaveAttribute('href', `/esports-assessments/reports/${REPORT_ID}`)
  expect(requests).toHaveLength(1)
  expect(new URL(requests[0], 'https://erp.test').searchParams.get('person_id')).toBe(PERSON_ID)
  expect(screen.getByRole('link', { name: '查看全部已授权报告' })).toHaveAttribute('href', `/esports-assessments/history?view=reports&person_id=${PERSON_ID}`)
})
it('shows a bounded generic panel error on private 404', async () => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () => privateResponse()))
  renderAssessmentPage(<><h1>人员档案仍可用</h1><AssessmentReportsPanel personId={PERSON_ID} /></>, { initialEntries: ['/'], account: studentFixture })
  expect(await screen.findByRole('alert')).toHaveTextContent('报告不存在或无权查看')
  expect(screen.getByRole('heading', { name: '人员档案仍可用' })).toBeVisible()
  expect(screen.queryByText('不得泄露的姓名')).not.toBeInTheDocument()
})
it('does not load reports for a take-only or legacy people reader', async () => {
  const requests = vi.fn<typeof fetch>(async () => privateResponse()); vi.stubGlobal('fetch', requests)
  renderAssessmentPage(<AssessmentReportsPanel personId={PERSON_ID} />, { initialEntries: ['/'], account: { ...studentFixture, scoped_permissions: ['esports_assessments.take', 'people.read'] } })
  expect(await screen.findByText('暂无天赋评测读取权限')).toBeVisible()
  expect(requests).not.toHaveBeenCalled()
})

it('never renders a late response from a previously selected person', async () => {
  const other = '77777777-7777-4777-8777-777777777778'
  let resolveFirst!: (response: Response) => void
  const first = new Promise<Response>(resolve => { resolveFirst = resolve })
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async input => {
    const id = new URL(String(input), 'https://erp.test').searchParams.get('person_id')
    return id === PERSON_ID ? first : jsonResponse({ items: [], visible_total: 0, next_cursor: null })
  }))
  function SwitchingPerson() {
    const [id, setId] = useState(PERSON_ID)
    return <><button onClick={() => setId(other)}>切换人员</button><AssessmentReportsPanel personId={id} /></>
  }
  renderAssessmentPage(<SwitchingPerson />, { initialEntries: ['/'], account: studentFixture })
  await screen.findByText('正在读取已授权报告…')
  await userEvent.click(screen.getByRole('button', { name: '切换人员' }))
  await screen.findByText('暂无已完成报告')
  await act(async () => { resolveFirst(jsonResponse({ items: [summaryFixture()], visible_total: 1, next_cursor: null })); await first })
  expect(screen.queryByText(/报告合成学员/)).not.toBeInTheDocument()
  expect(screen.getByText('暂无已完成报告')).toBeVisible()
  expect(screen.getByRole('link', { name: '查看全部已授权报告' })).toHaveAttribute('href', `/esports-assessments/history?view=reports&person_id=${other}`)
})
