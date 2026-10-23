import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setCsrfToken } from '../api/client'
import type { ProblemDetailsError } from '../api/client'
import { assessmentApi } from './api'
import {
  fixtureAttempt,
  fixtureConfig,
  fixtureEnvironment,
  fixtureQuality,
  fixtureRaw,
  fixtureSettings,
  fixtureSuiteConfig,
} from './testSupport/fixtures'

const SESSION_ID = '33333333-3333-4333-8333-333333333333'
const ATTEMPT_ID = '22222222-2222-4222-8222-222222222222'
const RESULT_ID = '55555555-5555-4555-8555-555555555555'
const REPORT_ID = '66666666-6666-4666-8666-666666666666'
const PERSON_ID = '77777777-7777-4777-8777-777777777777'
const OPERATION_KEY = '44444444-4444-4444-8444-444444444444'
const NOW = '2026-09-22T00:00:00.000Z'

function response(value: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

function session(overrides: Record<string, unknown> = {}) {
  return {
    id: SESSION_ID,
    suite_id: 'pc-fps',
    suite_config: fixtureSuiteConfig('pc-fps'),
    status: 'in_progress',
    members: ['precision', 'tracking', 'switching', 'recognition'],
    results: [],
    report_id: null,
    ...overrides,
  }
}

function scored() {
  return { metrics: { accuracy: 50 }, score: 50, grade: 'medium', quality_flags: [] }
}

function formalSave() {
  return {
    mode: 'formal',
    result_id: RESULT_ID,
    scored: scored(),
    session: session({
      results: [{ result_id: RESULT_ID, test_id: 'precision', scored: scored() }],
    }),
  }
}

function report() {
  const tests = ['precision', 'tracking', 'switching', 'recognition'] as const
  const suite = {
    suite_id: 'pc-fps',
    catalog_version: 'pc-fps-v1',
    test_ids: tests,
    tasks: Object.fromEntries(tests.map((test) => [test, fixtureConfig('pc-fps', test)])),
  }
  return {
    id: REPORT_ID,
    session_id: SESSION_ID,
    suite_id: 'pc-fps',
    suite,
    person: { person_id: PERSON_ID, display_name: '合成学员' },
    completed_at: NOW,
    results: tests.map((test, index) => ({
      result_id: `00000000-0000-4000-8000-${String(index + 900).padStart(12, '0')}`,
      test_id: test,
      config: fixtureConfig('pc-fps', test),
      settings: fixtureSettings('pc-fps'),
      environment: fixtureEnvironment('pc-fps'),
      scored: scored(),
    })),
    recommendations: [],
    comparisons: tests.map((test) => ({
      test_id: test,
      previous_report_id: null,
      score_delta: null,
      non_comparable_reasons: ['暂无历史记录'],
    })),
  }
}

beforeEach(() => {
  setCsrfToken('assessment-csrf')
  sessionStorage.clear()
})
afterEach(() => {
  setCsrfToken(null)
  sessionStorage.clear()
  vi.unstubAllGlobals()
})

describe('assessment API route mapping', () => {
  it('maps every read to the independent prefix with exact cursor filter names', async () => {
    const called: string[] = []
    vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input) => {
      const url = String(input)
      called.push(url)
      if (url.endsWith('/esports-assessments/context')) {
        return response({
          server_now: NOW,
          can_take: true,
          can_read_self: true,
          can_read_reports: true,
          available_suites: ['pc-fps'],
          own_in_progress_sessions: [session()],
          active_run: null,
        })
      }
      if (url.endsWith('/esports-assessments/suites')) {
        return response({ items: [report().suite] })
      }
      if (url.includes('/esports-assessments/recovery?')) {
        return response({ items: [], next_cursor: null, visible_total: 0 })
      }
      if (url.includes('/esports-assessments/history?')) {
        return response({ items: [], next_cursor: null, visible_total: 0 })
      }
      if (url.includes('/esports-assessments/reports?')) {
        return response({ items: [], next_cursor: null, visible_total: 0 })
      }
      if (url.endsWith(`/esports-assessments/reports/${REPORT_ID}`)) return response(report())
      throw new Error(`Unexpected request: ${url}`)
    }))

    await assessmentApi.context()
    await assessmentApi.suites()
    await assessmentApi.recovery({ cursor: 'recovery-cursor', limit: 20 })
    await assessmentApi.history({ mode: 'practice', cursor: 'history-cursor', limit: 40 })
    await assessmentApi.reports({
      person_id: PERSON_ID,
      suite_id: 'pc-fps',
      date_from: '2026-09-01',
      date_to: '2026-09-22',
      cursor: 'report-cursor',
      limit: 10,
    })
    await assessmentApi.report(REPORT_ID)

    expect(called).toEqual([
      '/api/v1/esports-assessments/context',
      '/api/v1/esports-assessments/suites',
      '/api/v1/esports-assessments/recovery?cursor=recovery-cursor&limit=20',
      '/api/v1/esports-assessments/history?mode=practice&cursor=history-cursor&limit=40',
      `/api/v1/esports-assessments/reports?person_id=${PERSON_ID}&suite_id=pc-fps&date_from=2026-09-01&date_to=2026-09-22&cursor=report-cursor&limit=10`,
      `/api/v1/esports-assessments/reports/${REPORT_ID}`,
    ])
    expect(called.every((url) => !url.includes('/api/v1/api/v1/'))).toBe(true)
  })

  it('maps formal and practice lifecycle writes without changing operation keys', async () => {
    const calls: Array<{ url: string; method: string; body: unknown }> = []
    vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      const method = init?.method ?? 'GET'
      const body = init?.body ? JSON.parse(String(init.body)) as unknown : null
      calls.push({ url, method, body })
      if (url.endsWith('/sessions') && method === 'POST') return response(session(), 201)
      if (url.endsWith(`/sessions/${SESSION_ID}`) && method === 'GET') return response(session())
      if (url.endsWith(`/sessions/${SESSION_ID}/attempts`)) return response(fixtureAttempt(), 201)
      if (url.endsWith(`/attempts/${ATTEMPT_ID}`) && method === 'GET') {
        return response({ attempt: fixtureAttempt(), saved: null })
      }
      if (url.endsWith(`/attempts/${ATTEMPT_ID}/result`)) return response(formalSave(), 201)
      if (url.endsWith(`/attempts/${ATTEMPT_ID}/interrupt`)) {
        return response(fixtureAttempt({ state: 'interrupted' }))
      }
      if (url.endsWith(`/sessions/${SESSION_ID}/abandon`)) {
        return response(session({ status: 'abandoned' }))
      }
      if (url.endsWith('/practice-runs') && method === 'POST') {
        return response(fixtureAttempt({ mode: 'practice', session_id: null }), 201)
      }
      if (url.endsWith(`/practice-runs/${ATTEMPT_ID}`) && method === 'GET') {
        return response({
          attempt: fixtureAttempt({ mode: 'practice', session_id: null }),
          saved: null,
        })
      }
      if (url.endsWith(`/practice-runs/${ATTEMPT_ID}/result`)) {
        return response({
          mode: 'practice',
          result_id: RESULT_ID,
          scored: { metrics: {}, score: null, grade: null, quality_flags: [] },
          session: null,
        }, 201)
      }
      if (url.endsWith(`/practice-runs/${ATTEMPT_ID}/interrupt`)) {
        return response(fixtureAttempt({ mode: 'practice', session_id: null, state: 'interrupted' }))
      }
      throw new Error(`Unexpected request: ${method} ${url}`)
    }))

    const sessionCreate = { suite_id: 'pc-fps' as const, operation_key: OPERATION_KEY }
    const attemptCreate = {
      test_id: 'precision' as const,
      settings: fixtureSettings('pc-fps'),
      environment: fixtureEnvironment('pc-fps'),
      operation_key: OPERATION_KEY,
    }
    const resultSubmit = {
      operation_key: OPERATION_KEY,
      raw: fixtureRaw('pc-fps', 'precision', false),
      quality: fixtureQuality('mouse'),
    }
    await assessmentApi.createSession(sessionCreate)
    await assessmentApi.readSession(SESSION_ID)
    await assessmentApi.startAttempt(SESSION_ID, attemptCreate)
    await assessmentApi.readAttempt(ATTEMPT_ID)
    await assessmentApi.submitResult(ATTEMPT_ID, resultSubmit)
    await assessmentApi.interruptAttempt(ATTEMPT_ID, { operation_key: OPERATION_KEY })
    await assessmentApi.abandonSession(SESSION_ID, { operation_key: OPERATION_KEY })
    await assessmentApi.startPractice({
      ...attemptCreate,
      suite_id: 'pc-fps',
      difficulty: 3,
    })
    await assessmentApi.readPractice(ATTEMPT_ID)
    await assessmentApi.submitPractice(ATTEMPT_ID, resultSubmit)
    await assessmentApi.interruptPractice(ATTEMPT_ID, { operation_key: OPERATION_KEY })

    expect(calls.map(({ url }) => url)).toEqual([
      '/api/v1/esports-assessments/sessions',
      `/api/v1/esports-assessments/sessions/${SESSION_ID}`,
      `/api/v1/esports-assessments/sessions/${SESSION_ID}/attempts`,
      `/api/v1/esports-assessments/attempts/${ATTEMPT_ID}`,
      `/api/v1/esports-assessments/attempts/${ATTEMPT_ID}/result`,
      `/api/v1/esports-assessments/attempts/${ATTEMPT_ID}/interrupt`,
      `/api/v1/esports-assessments/sessions/${SESSION_ID}/abandon`,
      '/api/v1/esports-assessments/practice-runs',
      `/api/v1/esports-assessments/practice-runs/${ATTEMPT_ID}`,
      `/api/v1/esports-assessments/practice-runs/${ATTEMPT_ID}/result`,
      `/api/v1/esports-assessments/practice-runs/${ATTEMPT_ID}/interrupt`,
    ])
    expect(calls.filter(({ method }) => method === 'POST').every(({ body }) =>
      (body as { operation_key?: string }).operation_key === OPERATION_KEY,
    )).toBe(true)
  })
})

it('rejects a valid-shaped response whose identity differs from the requested route', async () => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () => response(session({
    id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  }))))

  await expect(assessmentApi.readSession(SESSION_ID)).rejects.toThrow('服务响应与请求资源不一致')
})

it('rejects a report whose internal suite identity differs from the route response', async () => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () => response({
    ...report(),
    suite_id: 'mobile-fps',
  })))

  await expect(assessmentApi.report(REPORT_ID)).rejects.toThrow('服务响应与请求资源不一致')
})

it('rejects malformed API data and never treats it as a saved result', async () => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () => response({
    mode: 'formal',
    result_id: RESULT_ID,
    scored: { metrics: {}, score: 101, grade: 'low', quality_flags: [] },
    session: session(),
  })))

  await expect(assessmentApi.submitResult(ATTEMPT_ID, {
    operation_key: OPERATION_KEY,
    raw: fixtureRaw('pc-fps', 'precision', false),
    quality: fixtureQuality('mouse'),
  })).rejects.toThrow('服务返回的数据格式无效')
  expect(
    Array.from({ length: sessionStorage.length }, (_, index) => sessionStorage.key(index))
      .filter((key) => key?.startsWith('erp:esports-assessments:')),
  ).toEqual([])
})

it('applies report date filters in the ERP Shanghai calendar', async () => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () => response({
    items: [{
      id: REPORT_ID,
      session_id: SESSION_ID,
      suite_id: 'pc-fps',
      person: { person_id: PERSON_ID, display_name: '合成学员' },
      completed_at: '2026-09-21T16:30:00.000Z',
    }],
    next_cursor: null,
    visible_total: 1,
  })))

  const page = await assessmentApi.reports({
    date_from: '2026-09-22',
    date_to: '2026-09-22',
  })

  expect(page.items).toHaveLength(1)
})

it('rejects invalid cursor and date ranges before making a request', async () => {
  const fetchMock = vi.fn<typeof fetch>()
  vi.stubGlobal('fetch', fetchMock)

  await expect(assessmentApi.recovery({ cursor: '', limit: 20 })).rejects.toBeDefined()
  await expect(assessmentApi.reports({
    date_from: '2026-09-23',
    date_to: '2026-09-22',
  })).rejects.toBeDefined()
  expect(fetchMock).not.toHaveBeenCalled()
})

it('a 429 retry reuses the caller operation key', async () => {
  const bodies: string[] = []
  let count = 0
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (_input, init) => {
    bodies.push(String(init?.body))
    count += 1
    if (count === 1) {
      return response({
        type: 'about:blank',
        title: 'Too Many Requests',
        status: 429,
        detail: '操作过于频繁，请稍后重试。',
        code: 'esports_rate_limited',
        request_id: 'rate-limit-test',
      }, 429, { 'Retry-After': '3' })
    }
    return response(fixtureAttempt(), 201)
  }))
  const request = {
    test_id: 'precision' as const,
    settings: fixtureSettings('pc-fps'),
    environment: fixtureEnvironment('pc-fps'),
    operation_key: OPERATION_KEY,
  }

  await expect(assessmentApi.startAttempt(SESSION_ID, request)).rejects.toMatchObject({
    retryAfterSeconds: 3,
  } satisfies Partial<ProblemDetailsError>)
  await assessmentApi.startAttempt(SESSION_ID, request)

  expect(bodies.map((body) => JSON.parse(body).operation_key)).toEqual([
    OPERATION_KEY,
    OPERATION_KEY,
  ])
})
