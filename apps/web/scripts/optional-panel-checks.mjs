// Fixed acceptance assertion, never used by product code or to fabricate data.
export function assertOwnRequestDelta(beforeIds, afterIds, submittedId, posts) {
  const before = new Set(beforeIds), after = new Set(afterIds)
  const added = [...after].filter(id => !before.has(id))
  if (posts !== 1 || before.size !== beforeIds.length || after.size !== afterIds.length
    || after.size !== before.size + 1 || before.has(submittedId)
    || [...before].some(id => !after.has(id)) || added.length !== 1 || added[0] !== submittedId) {
    throw new Error('Expected exactly one new own request and one submission POST')
  }
}
