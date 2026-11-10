import { z } from "zod"

import { apiFetch, ProblemDetailsError } from "../api/client"

const uuidPattern = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
const uuid = z.string().regex(new RegExp(`^${uuidPattern}$`))
const commonEvent = { source_id: uuid, date: z.iso.date() }
const eventSchema = z.discriminatedUnion("kind", [
  z.strictObject({
    ...commonEvent, kind: z.literal("planned_checkout"),
    detail_path: z.string().regex(new RegExp(`^/housing/rooms/${uuidPattern}$`)).nullable().default(null),
    building_name: z.string(), room_number: z.string(),
  }),
  z.strictObject({
    ...commonEvent, kind: z.literal("enrollment_end"),
    detail_path: z.string().regex(new RegExp(`^/teaching/enrollments/${uuidPattern}$`)).nullable().default(null),
    project_name: z.string(), cohort_name: z.string(), enrollment_status: z.enum(["active", "completed"]),
  }),
])
const departureSchema = z.strictObject({
  available: z.boolean(), observed_at: z.iso.datetime({ offset: true }),
  business_date: z.iso.date(), timezone: z.literal("Asia/Shanghai"),
  next_refresh_at: z.iso.datetime({ offset: true }), window: z.enum(["today", "three_days"]),
  today_count: z.number().int().nonnegative(), three_day_count: z.number().int().nonnegative(),
  page: z.number().int().min(1), page_size: z.number().int().min(1).max(50), total: z.number().int().nonnegative(),
  items: z.array(z.strictObject({ person_id: uuid, full_name: z.string(), nickname: z.string().nullable(), events: z.array(eventSchema) })),
}).refine((value) => value.available || !(value.today_count || value.three_day_count || value.total || value.items.length))

export type DepartureReminders = z.infer<typeof departureSchema>
export type DepartureEvent = z.infer<typeof eventSchema>
export type DepartureWindow = DepartureReminders["window"]

export const departureKeys = {
  root: (accountId: string) => ["portal", accountId, "departures"] as const,
  page: (accountId: string, contextEpoch: number, window: DepartureWindow, page: number, pageSize: number) =>
    [...departureKeys.root(accountId), contextEpoch, window, page, pageSize] as const,
}

export async function getDepartureReminders(
  input: { window?: DepartureWindow; page?: number; pageSize?: number } = {},
  signal?: AbortSignal,
): Promise<DepartureReminders> {
  const params = new URLSearchParams({ window: input.window ?? "three_days", page: String(input.page ?? 1), page_size: String(input.pageSize ?? 20) })
  let raw: unknown
  try {
    raw = await apiFetch<unknown>(`/portal/departure-reminders?${params}`, { signal })
  } catch (error) {
    // Do not retain upstream body fragments, Zod errors, or causes in Query's cache.
    if ((error instanceof Error || error instanceof DOMException) && error.name === "AbortError") throw new DOMException("请求已取消", "AbortError")
    if (error instanceof ProblemDetailsError) {
      // Only the numeric status is needed by the existing access-revocation flow.
      throw new ProblemDetailsError({ type: "about:blank", title: "Request failed", status: error.problem.status, detail: "提醒暂时无法加载，请重试。", code: "departure_request_failed", request_id: "" })
    }
    // eslint-disable-next-line preserve-caught-error
    throw new Error("提醒暂时无法加载，请重试。")
  }
  const parsed = departureSchema.safeParse(raw)
  if (!parsed.success || parsed.data.window !== (input.window ?? "three_days") || parsed.data.page !== (input.page ?? 1) || parsed.data.page_size !== (input.pageSize ?? 20)) {
    throw new Error("提醒响应格式无效，请重试。")
  }
  return parsed.data
}
