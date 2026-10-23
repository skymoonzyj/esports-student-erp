import type { PracticeHistoryItem } from './apiContracts'

function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  if (value !== null && typeof value === 'object') return `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`).join(',')}}`
  return JSON.stringify(value)
}

export function comparePractice(first: PracticeHistoryItem, second: PracticeHistoryItem): { reasons: string[]; differences: Record<string, number> } {
  const reasons: string[] = []
  if (first.suite_id !== second.suite_id) reasons.push('套系不同')
  if (first.test_id !== second.test_id) reasons.push('项目不同')
  if (first.config.difficulty !== second.config.difficulty) reasons.push('难度不同')
  if (canonical(first.config) !== canonical(second.config)) reasons.push('规则配置不同')
  if (canonical(first.settings) !== canonical(second.settings)) reasons.push('控制设置不同')
  if (canonical(first.environment) !== canonical(second.environment)) reasons.push('设备或环境不同')
  if (first.scored.quality_flags.length || second.scored.quality_flags.length) reasons.push('存在质量警告')
  const differences: Record<string, number> = {}
  if (!reasons.length) for (const [key, value] of Object.entries(first.scored.metrics)) {
    const other = second.scored.metrics[key]
    if (typeof value === 'number' && typeof other === 'number') differences[key] = Number((other - value).toPrecision(15))
  }
  return { reasons, differences }
}
