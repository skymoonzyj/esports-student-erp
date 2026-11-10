import { useId } from "react"
import { Link } from "react-router-dom"

import type { useRentalReminders } from "./useRentalReminders"

import "./rentalReminders.css"

export function RentalRemindersPanel({ state }: { state: ReturnType<typeof useRentalReminders> }) {
  const titleId = useId()
  const { query, page, setPage, retry } = state
  const loading = query.isPending || query.isFetching
  const data = !loading && !query.isError ? query.data : undefined
  if (data?.available === false) return null
  const pageCount = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1
  return (
    <section className="rentalReminders" aria-labelledby={titleId} aria-busy={loading}>
      <header><div><h2 id={titleId}>租用资产到期提醒</h2><p>近七日（含第七天）到期及已逾期的有效租赁</p></div>{data?.available ? <Link to="/assets">{data.can_manage ? "管理资产与续期" : "查看资产台账"}</Link> : null}</header>
      {loading ? <p role="status">正在更新租期提醒</p> : query.isError ? (
        <div role="alert"><p>租期提醒暂时无法更新</p><button type="button" aria-label="重试租期提醒" onClick={retry}>重试</button></div>
      ) : data ? <>
        <p className="rentalBusinessDate">中国时间 · <time dateTime={data.business_date}>{data.business_date}</time></p>
        {data.items.length ? <ul aria-label="租期提醒资产" className="rentalReminderList">{data.items.map((item) => (
          <li key={item.asset_id}>
            <div><h3>{item.name}</h3><span>{item.asset_code}</span></div>
            <p><span>{item.person_name ?? "未分配"}</span> · <span>{item.location ?? "地点未填写"}</span></p>
            <div className="rentalExpiry"><time dateTime={item.rental_expires_on}>{item.rental_expires_on}</time><strong className={item.days_remaining < 0 ? "overdue" : "soon"}>{item.days_remaining < 0 ? `已逾期 ${-item.days_remaining} 天` : item.days_remaining === 0 ? "今日到期" : `${item.days_remaining} 天后到期`}</strong></div>
          </li>
        ))}</ul> : <p>暂无近七日到期或已逾期的租用资产</p>}
        {pageCount > 1 ? <nav className="rentalReminderPagination" aria-label="租期提醒分页"><button type="button" disabled={page <= 1} aria-label="上一页租期提醒" onClick={() => setPage(page - 1)}>上一页</button><p aria-live="polite">第 {data.page} / {pageCount} 页 · 共 {data.total} 件</p><button type="button" disabled={page >= pageCount} aria-label="下一页租期提醒" onClick={() => setPage(page + 1)}>下一页</button></nav> : null}
      </> : null}
    </section>
  )
}
