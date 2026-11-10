import { formatShanghaiDateTime } from '../teaching/datetime'
import { itemRankingReasonLabels, type ItemRankingMetric, type ItemRankingStanding, type ItemRankingView } from './itemContracts'
import './rankings.css'

export type ItemRankingPanelProps = {
  view: ItemRankingView | null | undefined
  loading: boolean
  error: unknown
  onRetry?: () => void
}

function metricValue(metric: ItemRankingMetric, value: number): string {
  return `${value.toFixed(metric.decimals)}${metric.unit ? ` ${metric.unit}` : ''}`
}

function standingValues(view: ItemRankingView, standing: ItemRankingStanding): string {
  return view.metrics.map(metric => `${metric.label} ${metricValue(metric, standing.values[metric.key])}`).join('；')
}

/** Presentation only: ranking services own requests, identity and all calculations. */
export function ItemRankingPanel({ view, loading, error, onRetry }: ItemRankingPanelProps) {
  const ranked = !error && view?.status === 'ranked' ? view : null
  const lastRow = ranked?.top_three.at(-1)
  const truncatedTie = lastRow && lastRow.rank + lastRow.tie_count - 1 > ranked!.top_three.length
  return (
    <section className="assessmentRanking itemRanking" aria-label="当前数据库排名">
      <h2>当前数据库排名</h2>
      <p>仅用于内部训练参考；每人每项取历史最佳，不替换本次已保存成绩或原综合排名。</p>
      {error ? (
        <div>
          <p role="alert">单项排名暂不可用，请稍后重试。</p>
          {onRetry ? <button type="button" disabled={loading} onClick={onRetry}>重试单项排名</button> : null}
        </div>
      ) : !view ? loading ? <p role="status">正在读取单项排名…</p> : null : (
        <>
          <p>{view.group_label}</p>
          {view.status === 'not_eligible' ? <p>{itemRankingReasonLabels[view.reason!]}</p> : ranked ? (
            <>
              <div className="assessmentRankingSummary">
                <p>本次成绩：第 {ranked.current!.rank} 名 · {standingValues(ranked, ranked.current!)}</p>
                <p>个人历史最佳：第 {ranked.best!.rank} 名 · {standingValues(ranked, ranked.best!)}</p>
              </div>
              <p>同组 {ranked.participant_count} 人；本次名次对照其他人的历史最佳，不被自己的旧成绩挤低。</p>
              <p>同分并列，后续名次跳位，例如 1、1、3；同分行的展示顺序不用于打破并列。</p>
              <div className="assessmentRankingTableWrap">
                <table>
                  <caption>同组历史最佳前三</caption>
                  <thead><tr><th>名次 / 参测者</th><th>历史最佳成绩</th><th>与本次成绩对照</th></tr></thead>
                  <tbody>{ranked.top_three.map((row, index) => (
                    <tr key={index}>
                      <td data-label="名次 / 参测者"><strong>第 {row.rank} 名</strong><span>{row.is_self ? '本人最佳' : '匿名参测者'}</span>{row.tie_count > 1 ? <small>{row.tie_count} 人同分</small> : null}</td>
                      <td data-label="历史最佳成绩">{ranked.metrics.map(metric => <span key={metric.key}>{metric.label} {metricValue(metric, row.values[metric.key])}</span>)}</td>
                      <td data-label="与本次成绩对照">{ranked.metrics.map(metric => {
                        const gap = row.gaps.find(candidate => candidate.key === metric.key)!
                        return <span key={metric.key}>{gap.state === 'equal' ? `本次相同（${metric.label}）` : `本次${gap.state === 'ahead' ? '领先' : '落后'} ${metricValue(metric, gap.amount)}`}</span>
                      })}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
              {truncatedTie ? <p>第 {lastRow!.rank} 名共 {lastRow!.tie_count} 人同分；前三仅展示 {ranked.top_three.length} 条，不代表全部同分人员。</p> : null}
            </>
          ) : null}
          <p>计算时间：<time dateTime={view.calculated_at}>{formatShanghaiDateTime(view.calculated_at)}</time>（中国时间）；新成绩可能改变排名，已保存的原始成绩不变。</p>
          {onRetry ? <button type="button" disabled={loading} onClick={onRetry}>{loading ? '正在刷新单项排名…' : '刷新单项排名'}</button> : null}
        </>
      )}
    </section>
  )
}
