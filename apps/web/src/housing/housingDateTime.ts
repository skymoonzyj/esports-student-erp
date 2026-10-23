import { dateInputToShanghaiIso, isoToDateInput } from "../shared/date"

export function toShanghaiDateInput(timestamp: string): string {
  return isoToDateInput(timestamp)
}

export function fromShanghaiDateInput(value: string): string {
  return dateInputToShanghaiIso(value)
}

export function toShanghaiDateTimeInput(timestamp: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(timestamp))
  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return `${value.year}-${value.month}-${value.day}T${value.hour}:${value.minute}`
}

export function fromShanghaiDateTimeInput(value: string): string {
  return `${value}:00+08:00`
}
