import { describe, expect, it } from 'vitest'

import {
  assessmentContextViewSchema,
  assessmentReportPageSchema,
  attemptDetailViewSchema,
  historyPageSchema,
  recoveryPageSchema,
  suiteCatalogViewSchema,
} from './apiContracts'
import {
  fixtureAttempt,
  fixtureConfig,
  fixtureEnvironment,
  fixtureSettings,
  fixtureSuiteConfig,
} from './testSupport/fixtures'

const SESSION_ID = '33333333-3333-4333-8333-333333333333'
const ATTEMPT_ID = '22222222-2222-4222-8222-222222222222'
const RESULT_ID = '55555555-5555-4555-8555-555555555555'
const REPORT_ID = '66666666-6666-4666-8666-666666666666'
const PERSON_ID = '77777777-7777-4777-8777-777777777777'
const NOW = '2026-09-22T00:00:00.000Z'

function inProgressSession() {
  return {
    id: SESSION_ID,
    suite_id: 'pc-fps',
    suite_config: fixtureSuiteConfig('pc-fps'),
    status: 'in_progress',
    members: ['precision', 'tracking', 'switching', 'recognition'],
    results: [],
    report_id: null,
  }
}

function practiceHistoryItem() {
  return {
    mode: 'practice',
    attempt_id: ATTEMPT_ID,
    session_id: null,
    result_id: RESULT_ID,
    suite_id: 'pc-fps',
    test_id: 'precision',
    completed_at: NOW,
    config: fixtureConfig('pc-fps', 'precision'),
    settings: fixtureSettings('pc-fps'),
    environment: fixtureEnvironment('pc-fps'),
    scored: { metrics: { accuracy: 0.5 }, score: null, grade: null, quality_flags: [] },
    report_id: null,
  }
}

describe('assessment context and suite catalog contracts', () => {
  it('parses effective capabilities, resumable rounds and the current run occupancy', () => {
    const value = {
      server_now: NOW,
      can_take: true,
      can_read_self: true,
      can_read_reports: true,
      available_suites: ['pc-fps', 'pc-moba'],
      own_in_progress_sessions: [inProgressSession()],
      active_run: {
        mode: 'formal',
        attempt_id: ATTEMPT_ID,
        session_id: SESSION_ID,
        suite_id: 'pc-fps',
        test_id: 'precision',
        run_until: '2026-09-22T00:01:30.000Z',
        submit_until: '2026-09-22T00:30:00.000Z',
      },
    }

    expect(assessmentContextViewSchema.parse(value)).toEqual(value)
  })

  it('rejects duplicate server availability and duplicate ongoing rounds for one suite', () => {
    const base = {
      server_now: NOW,
      can_take: true,
      can_read_self: true,
      can_read_reports: false,
      available_suites: ['pc-fps'],
      own_in_progress_sessions: [inProgressSession()],
      active_run: null,
    }
    expect(assessmentContextViewSchema.safeParse({
      ...base,
      available_suites: ['pc-fps', 'pc-fps'],
    }).success).toBe(false)
    expect(assessmentContextViewSchema.safeParse({
      ...base,
      own_in_progress_sessions: [inProgressSession(), { ...inProgressSession(), id: REPORT_ID }],
    }).success).toBe(false)
    expect(assessmentContextViewSchema.safeParse({
      ...base,
      active_run: {
        mode: 'practice',
        attempt_id: ATTEMPT_ID,
        session_id: null,
        suite_id: 'pc-fps',
        test_id: 'dodge',
        run_until: '2026-09-22T00:01:30.000Z',
        submit_until: '2026-09-22T00:30:00.000Z',
      },
    }).success).toBe(false)
    expect(assessmentContextViewSchema.safeParse({
      ...base,
      can_read_self: false,
    }).success).toBe(false)
  })

  it('accepts only unique frozen suites in the server catalog', () => {
    const pcFps = {
      suite_id: 'pc-fps',
      catalog_version: 'pc-fps-v1',
      test_ids: ['precision', 'tracking', 'switching', 'recognition'],
      tasks: Object.fromEntries(
        ['precision', 'tracking', 'switching', 'recognition'].map((test) => [test, fixtureConfig('pc-fps', test)]),
      ),
    }
    expect(suiteCatalogViewSchema.parse({ items: [pcFps] }).items).toHaveLength(1)
    expect(suiteCatalogViewSchema.safeParse({ items: [pcFps, pcFps] }).success).toBe(false)
  })
})

describe('reconciliation and list contracts', () => {
  it('requires a saved result exactly when an attempt is submitted', () => {
    const running = { attempt: fixtureAttempt(), saved: null }
    expect(attemptDetailViewSchema.parse(running)).toEqual(running)

    const submittedAttempt = fixtureAttempt({ state: 'submitted' })
    const saved = {
      mode: 'formal',
      result_id: RESULT_ID,
      scored: { metrics: { accuracy: 50 }, score: 50, grade: 'medium', quality_flags: [] },
      session: {
        ...inProgressSession(),
        results: [{ result_id: RESULT_ID, test_id: 'precision', scored: { metrics: { accuracy: 50 }, score: 50, grade: 'medium', quality_flags: [] } }],
      },
    }
    expect(attemptDetailViewSchema.safeParse({ attempt: submittedAttempt, saved: null }).success).toBe(false)
    expect(attemptDetailViewSchema.parse({ attempt: submittedAttempt, saved }).saved).toEqual(saved)
    expect(attemptDetailViewSchema.safeParse({
      attempt: submittedAttempt,
      saved: { ...saved, mode: 'practice', session: null, scored: { metrics: {}, score: null, grade: null, quality_flags: [] } },
    }).success).toBe(false)
  })

  it('keeps recovery metadata private and bounded', () => {
    const item = {
      mode: 'practice',
      attempt_id: ATTEMPT_ID,
      session_id: null,
      suite_id: 'pc-moba',
      test_id: 'dodge',
      state: 'awaiting_submission',
      issued_at: NOW,
      run_until: '2026-09-22T00:01:30.000Z',
      submit_until: '2026-09-22T00:30:00.000Z',
    }
    expect(recoveryPageSchema.parse({ items: [item], next_cursor: 'opaque', visible_total: 1 }).items)
      .toEqual([item])
    expect(recoveryPageSchema.safeParse({
      items: [{ ...item, raw: { kind: 'tracking' } }],
      next_cursor: null,
      visible_total: 1,
    }).success).toBe(false)
    expect(recoveryPageSchema.safeParse({
      items: [{ ...item, suite_id: 'pc-fps' }],
      next_cursor: null,
      visible_total: 1,
    }).success).toBe(false)
  })

  it('uses round-level formal history and frozen practice details', () => {
    const formal = {
      mode: 'formal',
      session: inProgressSession(),
      created_at: NOW,
      completed_at: null,
    }
    const practice = practiceHistoryItem()
    const parsed = historyPageSchema.parse({
      items: [formal, practice],
      next_cursor: null,
      visible_total: 2,
    })
    expect(parsed.items).toEqual([formal, practice])
    expect(historyPageSchema.safeParse({
      items: [{ ...practice, scored: { ...practice.scored, score: 100, grade: 'exceptional' } }],
      next_cursor: null,
      visible_total: 1,
    }).success).toBe(false)
  })

  it('parses authorized report summaries with visible totals', () => {
    const summary = {
      id: REPORT_ID,
      session_id: SESSION_ID,
      suite_id: 'pc-fps',
      person: { person_id: PERSON_ID, display_name: '合成学员' },
      completed_at: NOW,
    }
    expect(assessmentReportPageSchema.parse({
      items: [summary],
      next_cursor: null,
      visible_total: 1,
    }).items).toEqual([summary])
  })
})
