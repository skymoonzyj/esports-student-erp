import type { Asset, AssetOwnership } from "./api"
import { todayDateInput } from "../shared/date"

export type RentalPeriod = "week" | "month" | "quarter"
export const rentalPeriodLabels: Record<RentalPeriod, string> = { week: "一周（7天）", month: "一月（日历月）", quarter: "一季（3个日历月）" }

function parse(value: string): Date {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new Error("租期日期必须为 YYYY-MM-DD")
  const [year, month, day] = value.split("-").map(Number)
  const date = new Date(0)
  date.setUTCHours(0, 0, 0, 0)
  date.setUTCFullYear(year, month - 1, day)
  if (year < 1 || date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) throw new Error("租期日期无效")
  return date
}

export function validRentalDate(value: string): boolean {
  try { parse(value); return true } catch { return false }
}

export function renewalDate(value: string, period: RentalPeriod): string {
  const date = parse(value)
  if (period === "week") date.setUTCDate(date.getUTCDate() + 7)
  else {
    const day = date.getUTCDate()
    date.setUTCDate(1)
    date.setUTCMonth(date.getUTCMonth() + (period === "month" ? 1 : 3))
    const last = new Date(date)
    last.setUTCMonth(last.getUTCMonth() + 1, 0)
    date.setUTCDate(Math.min(day, last.getUTCDate()))
  }
  if (date.getUTCFullYear() > 9999) throw new Error("续期日期超出9999年，请选择较短周期")
  return `${String(date.getUTCFullYear()).padStart(4, "0")}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`
}

function rentalFactsValid(asset: AssetOwnership & { rental_revision?: number }): boolean {
  return asset.ownership_type === "rented" && !asset.rental_returned_on && !!asset.rental_started_on && validRentalDate(asset.rental_started_on) && !!asset.rental_expires_on && validRentalDate(asset.rental_expires_on) && asset.rental_started_on <= asset.rental_expires_on && Number.isInteger(asset.rental_revision) && (asset.rental_revision ?? -1) >= 0
}

export function rentalEligible(asset: AssetOwnership & { status: string; rental_revision?: number }): boolean {
  return asset.status !== "retired" && rentalFactsValid(asset) && asset.rental_started_on! <= todayDateInput()
}

export function rentalReturnEligible(asset: Asset): boolean {
  return !asset.current_assignment && rentalFactsValid(asset) && asset.rental_started_on! <= todayDateInput()
}
