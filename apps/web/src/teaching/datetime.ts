const DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/
const LOCAL_DATETIME_PATTERN = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/

type DateParts = { year: number; month: number; day: number }

function parseDate(value: string): DateParts {
  const match = DATE_PATTERN.exec(value)
  if (!match) throw new Error("日期格式无效")
  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  const probe = new Date(Date.UTC(year, month - 1, day))
  if (
    probe.getUTCFullYear() !== year ||
    probe.getUTCMonth() !== month - 1 ||
    probe.getUTCDate() !== day
  ) {
    throw new Error("日期格式无效")
  }
  return { year, month, day }
}

function twoDigits(value: number): string {
  return String(value).padStart(2, "0")
}

export function nextCalendarDate(value: string): string {
  const { year, month, day } = parseDate(value)
  const next = new Date(Date.UTC(year, month - 1, day + 1))
  return `${next.getUTCFullYear()}-${twoDigits(next.getUTCMonth() + 1)}-${twoDigits(next.getUTCDate())}`
}

export function shanghaiLocalToIso(value: string): string {
  const match = LOCAL_DATETIME_PATTERN.exec(value)
  if (!match) throw new Error("时间格式无效")
  parseDate(`${match[1]}-${match[2]}-${match[3]}`)
  const hour = Number(match[4])
  const minute = Number(match[5])
  const second = Number(match[6] ?? "0")
  if (hour > 23 || minute > 59 || second > 59) throw new Error("时间格式无效")
  return `${match[1]}-${match[2]}-${match[3]}T${match[4]}:${match[5]}:${twoDigits(second)}+08:00`
}

export function shanghaiDateStart(value: string): string {
  parseDate(value)
  return `${value}T00:00:00+08:00`
}

export function shanghaiDateEndExclusive(value: string): string {
  return `${nextCalendarDate(value)}T00:00:00+08:00`
}

export function initializeCohortDates(startDate: string, inclusiveEndDate: string) {
  parseDate(startDate)
  const endDate = nextCalendarDate(inclusiveEndDate)
  return {
    startsAt: `${startDate}T00:00`,
    endsAt: `${endDate}T00:00`,
    checkInAt: `${startDate}T00:00`,
    checkOutAt: `${endDate}T00:00`,
  }
}

function shanghaiParts(value: string): Record<string, string> {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) throw new Error("时间格式无效")
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date)
  return Object.fromEntries(parts.map((part) => [part.type, part.value]))
}

export function formatShanghaiDateTime(value: string): string {
  const parts = shanghaiParts(value)
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`
}

export function isoToShanghaiLocal(value: string): string {
  const parts = shanghaiParts(value)
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`
}

export function yuanToCents(value: string): number {
  const normalized = value.trim()
  if (!/^\d+(?:\.\d{1,2})?$/.test(normalized)) {
    throw new Error("金额最多保留两位小数")
  }
  const [yuan, fraction = ""] = normalized.split(".")
  const cents = Number(yuan) * 100 + Number(fraction.padEnd(2, "0"))
  if (!Number.isSafeInteger(cents)) throw new Error("金额超出允许范围")
  return cents
}

export function formatYuan(cents: number): string {
  return (cents / 100).toFixed(2)
}
