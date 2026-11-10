import { describe, expect, it } from 'vitest'
import { validArtifactIdentity } from '../../tests/helpers/esportsArtifactIdentity'

describe('sanitized browser evidence identity', () => {
  it('accepts a versioned fixed-board case identity without allowing path components', () => {
    expect(validArtifactIdentity('/tmp/test-artifacts', 'EA-PC-V5-001', { fixed_board: true })).toBe(true)
    expect(validArtifactIdentity('/tmp/test-artifacts', 'EA-PC-V5/001', { fixed_board: true })).toBe(false)
  })
  it.each(['gridshot_window', 'gridshot_fullscreen', 'sixshot_window', 'sixshot_fullscreen'])('retains a separate safe %s phase without permitting arbitrary suffixes', phase => {
    expect(validArtifactIdentity('/tmp/test-artifacts', 'EA-PC-V5-001', { fixed_board: true }, phase)).toBe(true)
    expect(validArtifactIdentity('/tmp/test-artifacts', 'EA-PC-V5-001', { fixed_board: true }, '../private')).toBe(false)
  })
  it.each(['lane_melee_death', 'lane_melee_respawn', 'lane_minimum_trial', 'lane_minimum_preflight'])('accepts the safe %s lifecycle evidence phase but not a URL', phase => {
    expect(validArtifactIdentity('/tmp/test-artifacts', 'EA-PC-LANE-001', { native_hero_death: true }, phase)).toBe(true)
    expect(validArtifactIdentity('/tmp/test-artifacts', 'EA-PC-LANE-001', { native_hero_death: true }, 'https://private.example/')).toBe(false)
  })
  const validCases: Record<string, boolean>[] = [{ real_active_60s: true }, { viewport_1440: true, viewport_1024: true }, { server_saved: true }]
  it.each(validCases)('accepts the actual timing and viewport boolean categories', categories => {
    expect(validArtifactIdentity('/tmp/test-artifacts', 'EA-PC-REAL-001', categories)).toBe(true)
  })
  it.each(['private@example.com', '../secret', 'unknown payload', 'a'.repeat(65)])('rejects free text or path-like category keys', key => {
    expect(validArtifactIdentity('/tmp/test-artifacts', 'EA-PC-REAL-001', { [key]: true })).toBe(false)
  })
  it('rejects non-boolean content, missing directory and unsafe case identity', () => {
    expect(validArtifactIdentity('/tmp/test-artifacts', 'EA-PC-REAL-001', { server_saved: 'private' } as unknown as Record<string, boolean>)).toBe(false)
    expect(validArtifactIdentity(undefined, 'EA-PC-REAL-001', { server_saved: true })).toBe(false)
    expect(validArtifactIdentity('/tmp/test-artifacts', '../private', { server_saved: true })).toBe(false)
  })
})
