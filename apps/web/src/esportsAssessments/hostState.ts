import type { AttemptView, PendingSubmission, ResultSaveView, TaskConfig } from './contracts'

export type HostPhase = 'intro' | 'preflight' | 'trial' | 'starting' | 'countdown' | 'running' | 'pending' | 'saved' | 'interrupted'
export const assessmentTestLabels = { precision: '定位精度', tracking: '连续跟踪', switching: '目标切换', recognition: '敌我识别', 'move-fire': '移动射击', 'last-hit': '补刀时机', 'orb-walk': '走砍节奏', dodge: '威胁躲避', 'map-awareness': '小地图注意', 'skill-aim': '技能瞄准', 'move-attack': '移动攻击' } as const

export function assessmentTaskLabel(config: TaskConfig): string {
  return config.params.kind === 'dodge_choice' ? '危险区域识别与躲避' : assessmentTestLabels[config.test_id]
}

export function acceptResponse(requestGeneration: number, currentGeneration: number, requestAccount: string, currentAccount: string): boolean {
  return requestGeneration === currentGeneration && requestAccount === currentAccount && requestAccount !== ''
}

export function remainingLeaseMs(view: AttemptView, elapsedSinceRequest: number): number {
  return Math.max(0, Date.parse(view.run_until) - Date.parse(view.server_now) - Math.max(0, elapsedSinceRequest))
}

export function matchesAttempt(pending: PendingSubmission, attempt: AttemptView): boolean {
  return pending.attempt_id === attempt.id && pending.mode === attempt.mode
    && pending.session_id === attempt.session_id && pending.suite_id === attempt.suite_id && pending.test_id === attempt.test_id
}

export function acceptsSave(attempt: AttemptView, saved: ResultSaveView): boolean {
  return saved.mode === attempt.mode && (saved.mode === 'practice' || (
    saved.session.id === attempt.session_id && saved.session.suite_id === attempt.suite_id
    && saved.session.results.some(result => result.result_id === saved.result_id && result.test_id === attempt.test_id)
  ))
}
