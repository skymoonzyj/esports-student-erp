import { useId } from "react"
import { useAuth } from "../auth/AuthProvider"
import { usePortalContext } from "./usePortalContext"
import { usePortalAccessError } from "./usePortalAccessError"
import { useRoomCardReminders } from "./useRoomCardReminders"
import "./roomCardReminders.css"

export function RoomCardRemindersPanel() {
  const { user } = useAuth()
  const authSignature = JSON.stringify([user?.account_id, user?.is_super_admin, [...(user?.permissions ?? [])].sort(), [...(user?.scoped_permissions ?? [])].sort()])
  return <ScopedRoomCardReminders key={authSignature} />
}

function ScopedRoomCardReminders() {
  const { user } = useAuth()
  const context = usePortalContext()
  const state = useRoomCardReminders({ accountId: user?.account_id ?? null, contextReady: context.isSuccess && !context.isFetching, contextEpoch: context.dataUpdatedAt })
  usePortalAccessError(context.error ?? state.query.error)
  return <RoomCardRemindersContent state={state} contextError={Boolean(context.error)} />
}

type ReminderViewState = Pick<ReturnType<typeof useRoomCardReminders>, "page" | "setPage" | "retry"> & {
  query: Pick<ReturnType<typeof useRoomCardReminders>["query"], "data" | "isPending" | "isFetching" | "isError">
}
export function RoomCardRemindersContent({ state, contextError = false }: { state: ReminderViewState; contextError?: boolean }) {
  const titleId = useId()
  const { query, page, setPage, retry } = state
  const loading = query.isPending || query.isFetching
  const failed = contextError || query.isError
  const data = !loading && !failed ? query.data : undefined
  if (data?.available === false) return null
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1
  return <section className="roomCardReminders" aria-labelledby={titleId} aria-busy={loading && !failed}>
    <header><h2 id={titleId}>房卡到期提醒</h2><p>七日内到期及已过期房卡 · 日期提醒不代表门锁状态</p></header>
    {failed ? <div role="alert"><p>房卡提醒暂时无法更新</p><button type="button" aria-label="重试房卡提醒" onClick={retry}>重试</button></div> : loading || !data ? <p role="status">正在更新房卡提醒…</p> : <>
      <p>中国时间 · <time dateTime={data.business_date}>{data.business_date}</time> · 到期当天仍有效</p>
      {data.items.length ? <ul className="roomCardReminders__people" aria-label="房卡提醒人员">{data.items.map((card) => <li key={card.stay_id}>
        <article><h3>{card.person_name}</h3>{card.nickname ? <p>{card.nickname}</p> : null}<p>{card.building_name} · {card.room_number}</p>
          <p>到期日：<time dateTime={card.expires_on}>{card.expires_on}</time></p>
          <strong className={card.days_remaining < 0 ? "roomCardReminders__expired" : "roomCardReminders__due"}>{card.days_remaining < 0 ? `已过期 ${Math.abs(card.days_remaining)} 天` : card.days_remaining === 0 ? "今日到期" : `${card.days_remaining} 天后到期`}</strong>
        </article>
      </li>)}</ul> : <p>暂无七日内到期或已过期的房卡</p>}
      {pages > 1 ? <nav aria-label="房卡提醒分页"><button type="button" disabled={page <= 1} aria-label="上一页房卡提醒" onClick={() => setPage(page - 1)}>上一页</button><span aria-live="polite">第 {data.page} / {pages} 页 · 共 {data.total} 人</span><button type="button" disabled={page >= pages} aria-label="下一页房卡提醒" onClick={() => setPage(page + 1)}>下一页</button></nav> : null}
    </>}
  </section>
}
