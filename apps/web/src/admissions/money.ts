const wholeYuanFormatter = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 0,
  useGrouping: true,
})

export function formatCny(cents: number | null): string {
  if (cents === null) return "—"
  if (!Number.isSafeInteger(cents)) {
    throw new RangeError("CNY cents must be a safe integer")
  }

  const absoluteCents = Math.abs(cents)
  const wholeYuan = Math.floor(absoluteCents / 100)
  const fractionalCents = String(absoluteCents % 100).padStart(2, "0")
  const sign = cents < 0 ? "-" : ""
  return `${sign}¥${wholeYuanFormatter.format(wholeYuan)}.${fractionalCents}`
}
