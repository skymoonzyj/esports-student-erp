import { z } from "zod"
import { apiFetch, ProblemDetailsError } from "../api/client"

const schema = z.strictObject({
  available: z.boolean(), observed_at: z.iso.datetime({ offset: true }), business_date: z.iso.date(), next_refresh_at: z.iso.datetime({ offset: true }),
  items: z.array(z.strictObject({ stay_id: z.string(), person_id: z.string(), person_name: z.string(), nickname: z.string().nullable(), room_number: z.string(), building_name: z.string(), issued_on: z.iso.date(), expires_on: z.iso.date(), days_remaining: z.number().int().max(7) })),
  total: z.number().int().nonnegative(), page: z.number().int().min(1), page_size: z.number().int().min(1).max(50),
}).refine((value) => value.available || !(value.total || value.items.length))
export type RoomCardReminders = z.infer<typeof schema>
export const roomCardReminderKeys = { root: (accountId: string) => ["portal", accountId, "room-card-reminders"] as const, page: (accountId: string, epoch: number, page: number) => ["portal", accountId, "room-card-reminders", epoch, page] as const }
export async function getRoomCardReminders(page = 1, signal?: AbortSignal): Promise<RoomCardReminders> {
  let raw: unknown
  try {
    raw = await apiFetch<unknown>(`/portal/room-card-reminders?page=${page}&page_size=20`, { signal })
  } catch (error) {
    if ((error instanceof Error || error instanceof DOMException) && error.name === "AbortError") throw new DOMException("请求已取消", "AbortError")
    if (error instanceof ProblemDetailsError) throw new ProblemDetailsError({ type: "about:blank", title: "Request failed", status: error.problem.status, detail: "房卡提醒暂时无法加载，请重试。", code: "room_card_reminders_failed", request_id: "" })
    // Deliberately exclude upstream response fragments from the private query cache.
    // eslint-disable-next-line preserve-caught-error
    throw new Error("房卡提醒暂时无法加载，请重试。")
  }
  const parsed = schema.safeParse(raw)
  if (!parsed.success || parsed.data.page !== page || parsed.data.page_size !== 20) throw new Error("房卡提醒响应格式无效，请重试。")
  return parsed.data
}
