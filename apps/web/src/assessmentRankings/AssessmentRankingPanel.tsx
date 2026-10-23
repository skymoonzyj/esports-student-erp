import { useQuery, type QueryKey } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ProblemDetailsError } from '../api/client'
import { formatShanghaiDate } from '../teaching/datetime'
import { parseAssessmentRanking } from './contracts'
import './rankings.css'

function denied(error: unknown): boolean {
  return error instanceof ProblemDetailsError && [403, 404].includes(error.problem.status)
}

const reasonLabels = {
  quality_warning: '本轮存在质量提示，不参与排名；请在稳定环境下完成新一轮测试。',
  incomplete_results: '本轮结果不完整，不参与排名。',
  incompatible_input: '本轮输入方式或排名元数据无法确认，不参与排名。',
}

export function AssessmentRankingPanel({ reportId, queryKey, fetchRanking, items, reportPath, hasQualityWarnings }: {
  reportId: string
  queryKey: QueryKey
  fetchRanking: (reportId: string, signal: AbortSignal) => Promise<unknown>
  items: readonly { id: string; name: string }[]
  reportPath: string
  hasQualityWarnings: boolean
}) {
  const ranking = useQuery({
    queryKey: [...queryKey, 'ranking'],
    queryFn: async ({ signal }) => {
      const value = parseAssessmentRanking(await fetchRanking(reportId, signal), reportId, items.map(item => item.id))
      if (hasQualityWarnings && value.status === 'ranked') throw new Error('带质量提示的报告不可排名。')
      return value
    },
    enabled: query => !denied(query.state.error),
    retry: false, gcTime: 0, refetchOnMount: 'always',
    refetchOnWindowFocus: query => !denied(query.state.error),
    refetchOnReconnect: query => !denied(query.state.error),
  })
  const value = ranking.data
  return (
    <section className="assessmentRanking" aria-label="内部训练排名">
      <h2>内部训练排名</h2>
      <p>仅用于内部训练参考，不代表官方评价、智力或职业潜力。</p>
      <p>每人取历史最佳的一整轮（各项评分算术平均最高），不拼接单项最高分；整体与单项均采用同一最佳整轮。</p>
      <p>仅在相同目录、规则、引擎版本及逐项输入方式的同组内实时计算，每人只计一次；新成绩可能改变排名，本报告原始成绩不变。</p>
      <p>同分并列，后续名次跳位，例如 1、1、3。</p>
      {ranking.isPending ? <p role="status">正在读取内部训练排名…</p> : ranking.isError ? (
        <div>
          <p role="alert">{denied(ranking.error) ? '排名不存在或无权查看。' : '排名暂不可用，请稍后重试。'}</p>
          {!denied(ranking.error) ? <button type="button" onClick={() => void ranking.refetch()} disabled={ranking.isFetching}>重试排名</button> : null}
        </div>
      ) : value?.status === 'not_eligible' ? <p>{reasonLabels[value.reason!]}</p> : value?.status === 'ranked' ? (
        <div>
          <h3>当前最佳整轮排名</h3>
          {value.best_report_id !== reportId ? (
            <p>当前报告为历史轮次；以下评分及排名来自当前最佳整轮，与本报告原始成绩不同。 <Link to={`${reportPath}/${value.best_report_id}`}>查看最佳整轮报告</Link></p>
          ) : <p>本报告即当前最佳整轮，以下为该整轮排名。</p>}
          <p>最佳整轮完成日期：<time dateTime={value.best_completed_at!}>{formatShanghaiDate(value.best_completed_at!)}</time></p>
          <div className="assessmentRankingSummary">
            <p>最佳整轮平均分：<strong>{value.overall_score!.toFixed(2)} 分</strong></p>
            <p>整体第 {value.overall_rank} 名 / 同组 {value.participant_count} 人</p>
          </div>
          <div className="assessmentRankingTableWrap">
            <table>
              <caption>最佳整轮各项排名</caption>
              <thead><tr><th>项目</th><th>最佳整轮评分</th><th>同组排名 / 人数</th></tr></thead>
              <tbody>{value.items.map((item, index) => <tr key={item.test_id}><th scope="row">{items[index].name}</th><td>{item.score} 分</td><td>第 {item.rank} 名 / {value.participant_count} 人</td></tr>)}</tbody>
            </table>
          </div>
          <p>计算日期：<time dateTime={value.calculated_at}>{formatShanghaiDate(value.calculated_at)}</time></p>
          <button type="button" disabled={ranking.isFetching} onClick={() => void ranking.refetch()}>{ranking.isFetching ? '正在刷新排名…' : '刷新排名'}</button>
        </div>
      ) : null}
    </section>
  )
}
