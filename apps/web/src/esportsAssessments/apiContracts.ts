import { z } from 'zod'

import {
  attemptViewSchema,
  controlSettingsSchema,
  environmentSnapshotSchema,
  modeSchema,
  practiceResultSchema,
  resultSaveViewSchema,
  sessionViewSchema,
  suiteMembers,
  suiteConfigSchema,
  suiteIdSchema,
  taskConfigSchema,
  testIdSchema,
  validateSettings,
} from './contracts'

const uuid = z.uuid()
const date = z.iso.datetime({ offset: true })
const cursor = z.string().min(1).max(2048).nullable()
const total = z.number().int().nonnegative()

function unique<T>(values: readonly T[]): boolean {
  return new Set(values).size === values.length
}

export const activeRunViewSchema = z.strictObject({
  mode: modeSchema,
  attempt_id: uuid,
  session_id: uuid.nullable(),
  suite_id: suiteIdSchema,
  test_id: testIdSchema,
  run_until: date,
  submit_until: date,
}).refine(
  (value) =>
    (value.mode === 'formal') === (value.session_id !== null)
    && Date.parse(value.submit_until) > Date.parse(value.run_until),
  'invalid active run identity or deadlines',
)

export const assessmentContextViewSchema = z.strictObject({
  server_now: date,
  can_take: z.boolean(),
  can_read_self: z.boolean(),
  can_read_reports: z.boolean(),
  available_suites: z.array(suiteIdSchema).max(4),
  own_in_progress_sessions: z.array(sessionViewSchema).max(4),
  active_run: activeRunViewSchema.nullable(),
}).superRefine((value, context) => {
  if (!unique(value.available_suites)) {
    context.addIssue({ code: 'custom', message: 'duplicate available suite' })
  }
  if (
    value.own_in_progress_sessions.some((session) => session.status !== 'in_progress')
    || !unique(value.own_in_progress_sessions.map((session) => session.suite_id))
  ) {
    context.addIssue({ code: 'custom', message: 'invalid ongoing session set' })
  }
  if (
    value.active_run?.mode === 'formal'
    && !value.own_in_progress_sessions.some(
      (session) =>
        session.id === value.active_run?.session_id
        && session.suite_id === value.active_run.suite_id,
    )
  ) {
    context.addIssue({ code: 'custom', message: 'active formal run is not an ongoing session' })
  }
  if (value.active_run && !suiteMembers[value.active_run.suite_id].includes(value.active_run.test_id)) {
    context.addIssue({ code: 'custom', message: 'active run test does not belong to suite' })
  }
  if (!value.can_read_self && (value.own_in_progress_sessions.length > 0 || value.active_run !== null)) {
    context.addIssue({ code: 'custom', message: 'self data returned without self read capability' })
  }
})

export const suiteCatalogViewSchema = z.strictObject({
  items: z.array(suiteConfigSchema).max(4),
}).refine(
  (value) => unique(value.items.map((suite) => suite.suite_id)),
  'duplicate suite catalog entry',
)

export const recoveryItemSchema = z.strictObject({
  mode: modeSchema,
  attempt_id: uuid,
  session_id: uuid.nullable(),
  suite_id: suiteIdSchema,
  test_id: testIdSchema,
  state: z.enum(['running', 'awaiting_submission']),
  issued_at: date,
  run_until: date,
  submit_until: date,
}).refine(
  (value) =>
    (value.mode === 'formal') === (value.session_id !== null)
    && Date.parse(value.run_until) > Date.parse(value.issued_at)
    && Date.parse(value.submit_until) > Date.parse(value.run_until),
  'invalid recovery identity or deadlines',
).refine(
  (value) => suiteMembers[value.suite_id].includes(value.test_id),
  'recovery test does not belong to suite',
)

export function cursorPageSchema<T extends z.ZodType>(itemSchema: T) {
  return z.strictObject({
    items: z.array(itemSchema).max(100),
    next_cursor: cursor,
    visible_total: total,
  }).refine(
    (value) => value.visible_total >= value.items.length,
    'visible total cannot be smaller than the page',
  )
}

export const recoveryPageSchema = cursorPageSchema(recoveryItemSchema)

export const attemptDetailViewSchema = z.strictObject({
  attempt: attemptViewSchema,
  saved: resultSaveViewSchema.nullable(),
}).superRefine((value, context) => {
  const submitted = value.attempt.state === 'submitted'
  if (submitted !== (value.saved !== null)) {
    context.addIssue({ code: 'custom', message: 'submitted attempt must have one saved result' })
    return
  }
  if (!value.saved) return
  if (value.saved.mode !== value.attempt.mode) {
    context.addIssue({ code: 'custom', message: 'saved result mode differs from attempt' })
    return
  }
  if (value.saved.mode === 'formal') {
    const session = value.saved.session
    if (
      session.id !== value.attempt.session_id
      || session.suite_id !== value.attempt.suite_id
      || !session.results.some(
        (result) =>
          result.result_id === value.saved?.result_id
          && result.test_id === value.attempt.test_id,
      )
    ) {
      context.addIssue({ code: 'custom', message: 'saved formal result differs from attempt' })
    }
  }
})

export const formalHistoryItemSchema = z.strictObject({
  mode: z.literal('formal'),
  session: sessionViewSchema,
  created_at: date,
  completed_at: date.nullable(),
}).refine(
  (value) => (value.session.status === 'completed') === (value.completed_at !== null),
  'formal completion timestamp differs from session status',
)

export const practiceHistoryItemSchema = z.strictObject({
  mode: z.literal('practice'),
  attempt_id: uuid,
  session_id: z.null(),
  result_id: uuid,
  suite_id: suiteIdSchema,
  test_id: testIdSchema,
  completed_at: date,
  config: taskConfigSchema,
  settings: controlSettingsSchema,
  environment: environmentSnapshotSchema,
  scored: practiceResultSchema,
  report_id: z.null(),
}).superRefine((value, context) => {
  if (value.config.suite_id !== value.suite_id || value.config.test_id !== value.test_id) {
    context.addIssue({ code: 'custom', message: 'practice configuration identity mismatch' })
    return
  }
  try {
    validateSettings(value.config, value.settings, value.environment)
  } catch {
    context.addIssue({ code: 'custom', message: 'invalid frozen practice settings' })
  }
})

export const historyItemSchema = z.discriminatedUnion('mode', [
  formalHistoryItemSchema,
  practiceHistoryItemSchema,
])
export const historyPageSchema = cursorPageSchema(historyItemSchema)

export const personDisplaySnapshotSchema = z.strictObject({
  person_id: uuid,
  display_name: z.string().min(1).max(200),
})

export const assessmentReportSummarySchema = z.strictObject({
  id: uuid,
  session_id: uuid,
  suite_id: suiteIdSchema,
  person: personDisplaySnapshotSchema,
  completed_at: date,
})
export const assessmentReportPageSchema = cursorPageSchema(assessmentReportSummarySchema)

export type ActiveRunView = z.infer<typeof activeRunViewSchema>
export type AssessmentContextView = z.infer<typeof assessmentContextViewSchema>
export type SuiteCatalogView = z.infer<typeof suiteCatalogViewSchema>
export type RecoveryItem = z.infer<typeof recoveryItemSchema>
export type RecoveryPage = z.infer<typeof recoveryPageSchema>
export type AttemptDetailView = z.infer<typeof attemptDetailViewSchema>
export type FormalHistoryItem = z.infer<typeof formalHistoryItemSchema>
export type PracticeHistoryItem = z.infer<typeof practiceHistoryItemSchema>
export type HistoryItem = z.infer<typeof historyItemSchema>
export type HistoryPage = z.infer<typeof historyPageSchema>
export type AssessmentReportSummary = z.infer<typeof assessmentReportSummarySchema>
export type AssessmentReportPage = z.infer<typeof assessmentReportPageSchema>
