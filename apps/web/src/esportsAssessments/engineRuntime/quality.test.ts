import { describe, expect, it } from 'vitest'

import { FrameQualityTracker } from './quality'

describe('FrameQualityTracker', () => {
  it('integrates only the final-frame overlap while retaining the true frame gap', () => {
    const tracker = new FrameQualityTracker()
    for (let i = 0; i < 299; i += 1) tracker.recordFrameGap(200)
    tracker.recordFrameGap(400, 200)
    expect(tracker.snapshot(60000)).toMatchObject({ max_frame_gap_ms: 400, long_frame_gap_total_ms: 60000 })
    expect(tracker.fatalFrameGap).toBe(false)
  })
  it('does not hide a fatal true gap behind a short overlap', () => {
    const tracker = new FrameQualityTracker()
    tracker.recordFrameGap(500.01, 1)
    expect(tracker.fatalFrameGap).toBe(true)
    expect(tracker.snapshot(60000)).toMatchObject({ max_frame_gap_ms: 500.01, long_frame_gap_total_ms: 1 })
  })
  it.each([NaN, Infinity, -1, 101])('rejects impossible observed overlap %s before mutating', overlap => {
    const tracker = new FrameQualityTracker()
    expect(() => tracker.recordFrameGap(100, overlap)).toThrow()
    expect(tracker.snapshot(60000).long_frame_gap_total_ms).toBe(0)
  })
  it('accumulates only gaps above 50ms and warns only above 3000ms', () => {
    const tracker = new FrameQualityTracker()
    tracker.recordFrameGap(50)
    tracker.recordFrameGap(1000)
    tracker.recordFrameGap(2000)

    expect(tracker.longFrameWarning).toBe(false)
    expect(tracker.snapshot(60_000)).toMatchObject({
      max_frame_gap_ms: 2000,
      long_frame_gap_total_ms: 3000,
    })

    tracker.recordFrameGap(50.01)

    expect(tracker.longFrameWarning).toBe(true)
    expect(tracker.snapshot(60_000).long_frame_gap_total_ms).toBe(3050.01)
  })

  it('marks a single frame gap above 500ms as fatal', () => {
    const tracker = new FrameQualityTracker()

    tracker.recordFrameGap(500)
    expect(tracker.fatalFrameGap).toBe(false)

    tracker.recordFrameGap(500.01)
    expect(tracker.fatalFrameGap).toBe(true)
  })

  it('reports independent mouse, touch, mixed, and unknown input', () => {
    const tracker = new FrameQualityTracker()
    expect(tracker.snapshot(1).input_kind).toBe('unknown')

    tracker.observeInput('mouse')
    expect(tracker.snapshot(1).input_kind).toBe('mouse')

    tracker.observeInput('touch')
    expect(tracker.snapshot(1).input_kind).toBe('mixed')
  })

  it('preserves hidden and interruption evidence in the submit DTO', () => {
    const tracker = new FrameQualityTracker()
    tracker.markHidden()
    tracker.markInterrupted()

    expect(tracker.snapshot(12_345)).toEqual({
      duration_ms: 12_345,
      max_frame_gap_ms: 0,
      long_frame_gap_total_ms: 0,
      input_kind: 'unknown',
      hidden: true,
      interrupted: true,
    })
  })

  it('rejects invalid measured values rather than clamping them', () => {
    const tracker = new FrameQualityTracker()

    expect(() => tracker.recordFrameGap(Number.NaN)).toThrow()
    expect(() => tracker.recordFrameGap(-1)).toThrow()
    expect(() => tracker.snapshot(60_001)).toThrow()
  })
})
