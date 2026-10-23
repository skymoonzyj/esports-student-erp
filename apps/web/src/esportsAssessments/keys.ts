import type { Mode, SuiteId } from './contracts'

export const assessmentKeys = {
  root: (account: string) => ['esports-assessments', account] as const,
  context: (account: string) => [...assessmentKeys.root(account), 'context'] as const,
  suites: (account: string) => [...assessmentKeys.root(account), 'suites'] as const,
  session: (account: string, suite: SuiteId, id: string) => [
    ...assessmentKeys.root(account),
    'formal',
    suite,
    'session',
    id,
  ] as const,
  attempt: (account: string, mode: Mode, suite: SuiteId, id: string) => [
    ...assessmentKeys.root(account),
    mode,
    suite,
    'attempt',
    id,
  ] as const,
  practice: (account: string, suite: SuiteId, id: string) => [
    ...assessmentKeys.root(account),
    'practice',
    suite,
    'run',
    id,
  ] as const,
  recovery: (account: string) => [...assessmentKeys.root(account), 'recovery'] as const,
  history: (account: string, mode: Mode) => [
    ...assessmentKeys.root(account),
    mode,
    'history',
  ] as const,
  reports: (account: string) => [...assessmentKeys.root(account), 'reports'] as const,
  report: (account: string, id: string) => [
    ...assessmentKeys.root(account),
    'reports',
    id,
  ] as const,
}
