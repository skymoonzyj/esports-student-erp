import { z } from "zod"

import { apiFetch, ProblemDetailsError } from "../api/client"

const rentalSchema = z.strictObject({
  available: z.boolean(), can_manage: z.boolean(),
  observed_at: z.iso.datetime({ offset: true }), business_date: z.iso.date(),
  next_refresh_at: z.iso.datetime({ offset: true }),
  page: z.number().int().min(1), page_size: z.number().int().min(1).max(100), total: z.number().int().nonnegative(),
  items: z.array(z.strictObject({
    asset_id: z.uuid(), asset_code: z.string(), name: z.string(), location: z.string().nullable(), person_name: z.string().nullable(),
    rental_expires_on: z.iso.date(), days_remaining: z.number().int(), rental_revision: z.number().int().nonnegative(),
  })),
}).refine((value) => value.available || !(value.can_manage || value.total || value.items.length))

export type RentalReminders = z.infer<typeof rentalSchema>
export const rentalReminderKeys = {
  root: (accountId: string) => ["portal", accountId, "rental-reminders"] as const,
  page: (accountId: string, epoch: number, page: number) => [...rentalReminderKeys.root(accountId), epoch, page, 20] as const,
}

export async function getRentalReminders(page = 1, signal?: AbortSignal): Promise<RentalReminders> {
  let raw: unknown
  try {
    raw = await apiFetch<unknown>(`/portal/rental-reminders?page=${page}&page_size=20`, { signal })
  } catch (error) {
    if ((error instanceof Error || error instanceof DOMException) && error.name === "AbortError") throw new DOMException("请求已取消", "AbortError")
    if (error instanceof ProblemDetailsError) {
      throw new ProblemDetailsError({ type: "about:blank", title: "Request failed", status: error.problem.status, detail: "租期提醒暂时无法加载，请重试。", code: "rental_reminders_failed", request_id: "" })
    }
    // eslint-disable-next-line preserve-caught-error
    throw new Error("租期提醒暂时无法加载，请重试。")
  }
  const parsed = rentalSchema.safeParse(raw)
  if (!parsed.success || parsed.data.page !== page || parsed.data.page_size !== 20) throw new Error("租期提醒响应格式无效，请重试。")
  return parsed.data
}
