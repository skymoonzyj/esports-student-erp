export const MAX_HOUSING_BATCH_SELECTION = 500

export function updateHousingSelection(
  current: ReadonlyMap<string, number>,
  roomId: string,
  version: number,
  checked: boolean,
): { next: ReadonlyMap<string, number>; limited: boolean } {
  if (!checked) {
    if (!current.has(roomId)) return { next: current, limited: false }
    const next = new Map(current)
    next.delete(roomId)
    return { next, limited: false }
  }
  if (!current.has(roomId) && current.size >= MAX_HOUSING_BATCH_SELECTION) {
    return { next: current, limited: true }
  }
  if (current.get(roomId) === version) return { next: current, limited: false }
  const next = new Map(current)
  next.set(roomId, version)
  return { next, limited: false }
}
