import { formatDateOnly } from "../shared/date"

import type { HousingSummary } from "./api"

export const housingStatusLabels: Record<HousingSummary["status"], string> = {
  unarranged: "未安排住宿",
  reserved: "已预约",
  check_in_pending: "待确认入住",
  checked_in: "已入住",
  checkout_pending: "待确认退房",
  checked_out: "已退房",
}

function Stay({ stay }: { stay: NonNullable<HousingSummary["stay"]> }) {
  const plannedDeparture = stay.duration_mode === "long_term"
    ? "长期入住"
    : stay.planned_check_out_at
      ? formatDateOnly(stay.planned_check_out_at)
      : "未确定"

  return (
    <div className="invitedStay">
      <p>{stay.building_name} / {stay.floor_name} / {stay.room_name}</p>
      <dl>
        <div>
          <dt>计划入住</dt>
          <dd>{stay.planned_check_in_at ? formatDateOnly(stay.planned_check_in_at) : "未确定"}</dd>
        </div>
        <div>
          <dt>计划离开</dt>
          <dd>{plannedDeparture}</dd>
        </div>
        <div>
          <dt>实际入住</dt>
          <dd>{stay.actual_check_in_at ? formatDateOnly(stay.actual_check_in_at) : "—"}</dd>
        </div>
        <div>
          <dt>实际离开</dt>
          <dd>{stay.actual_check_out_at ? formatDateOnly(stay.actual_check_out_at) : "—"}</dd>
        </div>
      </dl>
    </div>
  )
}

export function InvitedHousingSummary({ summary }: { summary: HousingSummary }) {
  return (
    <section aria-label="住宿摘要">
      <p className="invitedStatus">{housingStatusLabels[summary.status]}</p>
      {summary.stay ? <Stay stay={summary.stay} /> : null}
      {summary.next_planned_stay ? (
        <div>
          <h3>下一次计划</h3>
          <Stay stay={summary.next_planned_stay} />
        </div>
      ) : null}
    </section>
  )
}
