const DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/

type DateParts = { year: number; month: number; day: number }

/**
 * Parse a user-entered calendar date without allowing JavaScript's permissive
 * date parser to silently roll invalid values into another day.
 */
export function parseDateInput(value: string): DateParts {
  const match = DATE_PATTERN.exec(value.trim())
  if (!match) throw new Error("日期必须使用 YYYY-MM-DD 格式")
  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  const probe = new Date(Date.UTC(year, month - 1, day))
  if (
    probe.getUTCFullYear() !== year ||
    probe.getUTCMonth() !== month - 1 ||
    probe.getUTCDate() !== day
  ) {
    throw new Error("日期无效，请填写真实的年月日")
  }
  return { year, month, day }
}

export function isDateInput(value: string): boolean {
  try {
    parseDateInput(value)
    return true
  } catch {
    return false
  }
}

function twoDigits(value: number): string {
  return String(value).padStart(2, "0")
}

export function nextDateInput(value: string): string {
  const { year, month, day } = parseDateInput(value)
  const next = new Date(Date.UTC(year, month - 1, day + 1))
  return `${next.getUTCFullYear()}-${twoDigits(next.getUTCMonth() + 1)}-${twoDigits(next.getUTCDate())}`
}

export function previousDateInput(value: string): string {
  const { year, month, day } = parseDateInput(value)
  const previous = new Date(Date.UTC(year, month - 1, day - 1))
  return `${previous.getUTCFullYear()}-${twoDigits(previous.getUTCMonth() + 1)}-${twoDigits(previous.getUTCDate())}`
}

export function todayDateInput(now = new Date()): string {
  return formatDateOnly(now)
}

/** Convert a date input to the API's existing Shanghai-midnight ISO form. */
export function dateInputToShanghaiIso(value: string): string {
  parseDateInput(value)
  return `${value}T00:00:00+08:00`
}

/** Convert an inclusive date filter to an exclusive upper bound. */
export function dateInputToShanghaiEndExclusiveIso(value: string): string {
  return `${nextDateInput(value)}T00:00:00+08:00`
}

function shanghaiParts(
  value: string | Date,
  options: { includeTime?: boolean } = {},
): Record<string, string> {
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) throw new Error("时间格式无效")
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    ...(options.includeTime
      ? {
          hour: "2-digit" as const,
          minute: "2-digit" as const,
          second: "2-digit" as const,
          hourCycle: "h23" as const,
        }
      : {}),
  }).formatToParts(date)
  return Object.fromEntries(parts.map((part) => [part.type, part.value]))
}

/** Format an API timestamp or date as YYYY-MM-DD for business screens. */
export function formatDateOnly(value: string | Date | null | undefined): string {
  if (value === null || value === undefined || value === "") return "未设置"
  if (typeof value === "string" && DATE_PATTERN.test(value)) {
    // Keep date-only values stable regardless of the browser's local timezone.
    parseDateInput(value)
    return value
  }
  const parts = shanghaiParts(value)
  return `${parts.year}-${parts.month}-${parts.day}`
}

/** Parse an API timestamp into the value expected by <input type="date">. */
export function isoToDateInput(value: string | null | undefined): string {
  if (!value) return ""
  return formatDateOnly(value)
}

/**
 * Convert an exclusive end timestamp back to the inclusive calendar date that
 * users entered. New date-only records end at Shanghai midnight on the next
 * day; legacy records with a real time of day remain on their stored date.
 */
export function exclusiveEndToDateInput(value: string | Date | null | undefined): string {
  if (value === null || value === undefined || value === "") return ""
  if (typeof value === "string" && DATE_PATTERN.test(value)) {
    return previousDateInput(value)
  }
  const parts = shanghaiParts(value, { includeTime: true })
  const localDate = `${parts.year}-${parts.month}-${parts.day}`
  const isMidnight =
    parts.hour === "00" && parts.minute === "00" && parts.second === "00"
  return isMidnight ? previousDateInput(localDate) : localDate
}

/** Format an exclusive upper bound as the inclusive business end date. */
export function formatExclusiveEndDate(
  value: string | Date | null | undefined,
): string {
  return exclusiveEndToDateInput(value) || "未设置"
}

/**
 * Date/time formatting is intentionally kept separate for the audit screen.
 * Business screens use date-only values, while audit records retain the
 * precise Shanghai-local timestamp for accountability.
 */
export function formatAuditDateTime(value: string | Date | null | undefined): string {
  if (value === null || value === undefined || value === "") return "未设置"
  const date = value instanceof Date ? value : new Date(value)
  if (Number.isNaN(date.getTime())) return "时间无效"
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date)
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return `${values.year}-${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second}`
}
