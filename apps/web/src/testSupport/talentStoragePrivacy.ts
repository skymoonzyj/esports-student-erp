export function containsKnownPrivateValue(
  value: unknown,
  knownPrivateValues: ReadonlySet<string>,
): boolean {
  const seen = new WeakSet<object>()
  const visit = (current: unknown): boolean => {
    if (typeof current === "string") return knownPrivateValues.has(current)
    if (current === null || typeof current !== "object") return false
    if (seen.has(current)) return false
    seen.add(current)
    if (Array.isArray(current)) return current.some(visit)
    return Object.values(current).some(visit)
  }
  return visit(value)
}
