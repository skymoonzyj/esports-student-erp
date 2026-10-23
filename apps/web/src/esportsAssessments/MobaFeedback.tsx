import type { MobaFeedbackOutcome, MobaSceneSnapshot } from './engineRuntime/types'

const objectives = { 'last-hit': '补刀', attack: '攻击', checkpoint: '到位', dodge: '躲避', 'main-target': '主目标', 'map-event': '地图事件', 'skill-aim': '技能命中' }
const reasons = { early: '过早', late: '过晚', missed: '未完成', cancelled: '操作取消', hit: '受击', wrong: '选错' }
const progressLabels = { 'attack-ready': '可以出手', 'attack-windup': '出手中，移动会取消', 'attack-completed': '攻击已完成', 'attack-cancelled': '攻击已被移动取消' }
function outcomeLabel(outcome: MobaFeedbackOutcome): string {
  return `第 ${outcome.round} 次${objectives[outcome.objective]}：${outcome.result === 'success' ? '成功' : outcome.reason ? reasons[outcome.reason] : '未完成'}`
}

/** A fixed footprint prevents feedback changes from moving or resizing the live stage. */
export function MobaFeedback({ scene, compact = false }: { scene: MobaSceneSnapshot | null; compact?: boolean }) {
  const feedback = scene?.feedback
  const danger = scene?.danger_choice
  const progress = feedback?.progress ? progressLabels[feedback.progress] : ''
  const latest = feedback?.outcomes.map(outcomeLabel).join('；') ?? ''
  const dangerState = danger?.phase === 'waiting' ? '等待安全区预警'
    : danger?.phase === 'warning' ? '请完整进入安全圆'
      : danger?.phase === 'active' ? '爆发期间保持安全'
        : danger?.phase === 'resolved' ? '本轮危险已结束' : ''
  const summary = feedback ? `第 ${feedback.round.current}/${feedback.round.total} 轮 · ${dangerState || progress || (feedback.round.status === 'active' ? '进行中' : feedback.round.status === 'success' ? '本轮成功' : '本轮未完成')}` : scene ? '旧版不提供实时反馈' : '试练开始后显示操作反馈'
  return <section aria-label="测试反馈" className={`assessmentMobaFeedback${compact ? ' assessmentMobaFeedbackCompact' : ''}`} title={`${summary}${latest ? `；${latest}` : ''}`}>
    <span className="assessmentMobaFeedbackState">{summary}</span>
    {latest && <span className="assessmentMobaFeedbackOutcome">{latest}</span>}
  </section>
}
