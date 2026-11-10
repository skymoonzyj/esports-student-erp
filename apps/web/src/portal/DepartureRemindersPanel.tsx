import { useId } from "react"
import { Link } from "react-router-dom"

import type { useDepartureReminders } from "./useDepartureReminders"

import "./departureReminders.css"

export function DepartureRemindersPanel({ state }: { state: ReturnType<typeof useDepartureReminders> }) {
  const titleId = useId()
  const { query, window, setWindow, page, setPage, retry } = state
  const loading = query.isPending || query.isFetching
  const data = !loading && !query.isError ? query.data : undefined
  if (data?.available === false) return null

  const pageCount = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1

  return (
    <section className="departureReminders" aria-labelledby={titleId} aria-busy={loading}>
      <header className="departureHeader">
        <div>
          <h2 id={titleId}>退宿／结营提醒</h2>
          <p>依据计划日期提示，不代表已实际离开</p>
        </div>
        <div className="departureWindows" role="group" aria-label="提醒日期范围">
          <button type="button" aria-pressed={window === "today"} disabled={loading} onClick={() => setWindow("today")}>
            今日{data ? ` ${data.today_count} 人` : ""}
          </button>
          <button type="button" aria-pressed={window === "three_days"} disabled={loading} onClick={() => setWindow("three_days")}>
            近三日{data ? ` ${data.three_day_count} 人` : ""}
          </button>
        </div>
      </header>

      {loading ? (
        <p className="departureStatus" role="status">正在更新退宿／结营提醒</p>
      ) : query.isError ? (
        <div className="departureError" role="alert">
          <p>退宿／结营提醒暂时无法更新</p>
          <button type="button" onClick={retry} aria-label="重试提醒">重试</button>
        </div>
      ) : data ? (
        <>
          <p className="departureDate">中国时间 · <time dateTime={data.business_date}>{data.business_date}</time> · 近三日含今天、明天、后天</p>
          {data.items.length === 0 ? (
            <p className="departureStatus">所选日期内暂无退宿或结营提醒</p>
          ) : (
            <ul className="departurePeople" aria-label="提醒人员">
              {data.items.map((person) => (
                <li key={person.person_id}>
                  <article className="departurePerson">
                    <h3>{person.full_name}{person.nickname ? <span className="departureNickname">{person.nickname}</span> : null}</h3>
                    <ul className="departureEvents">
                      {person.events.map((event) => {
                        const label = event.kind === "planned_checkout" ? "计划退宿" : "营期结束"
                        const today = event.date === data.business_date
                        return (
                          <li key={`${event.kind}:${event.source_id}`} className={today ? "departureEvent today" : "departureEvent"}>
                            <div className="departureEventHeading">
                              <strong>{label}</strong>
                              <time dateTime={event.date}>{event.date}</time>
                              {today ? <span className="departureToday">今日</span> : null}
                            </div>
                            <p>{event.kind === "planned_checkout" ? `${event.building_name} · ${event.room_number}` : `${event.project_name} · ${event.cohort_name}`}</p>
                            {event.kind === "enrollment_end" && event.enrollment_status === "completed" ? <span className="departureCompleted">教学已完成</span> : null}
                            {event.detail_path ? <Link to={event.detail_path} aria-label={`查看${label}详情：${person.full_name} ${event.date}`}>查看详情</Link> : null}
                          </li>
                        )
                      })}
                    </ul>
                  </article>
                </li>
              ))}
            </ul>
          )}
          {pageCount > 1 ? (
            <nav className="departurePagination" aria-label="提醒分页">
              <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)} aria-label="上一页提醒">上一页</button>
              <p aria-live="polite">第 {data.page} / {pageCount} 页 · 共 {data.total} 人</p>
              <button type="button" disabled={page >= pageCount} onClick={() => setPage(page + 1)} aria-label="下一页提醒">下一页</button>
            </nav>
          ) : null}
        </>
      ) : null}
    </section>
  )
}
