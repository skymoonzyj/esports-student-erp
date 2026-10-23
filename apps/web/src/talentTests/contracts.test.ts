import { describe, expect, it } from "vitest"

import { ENGINE_VERSIONS, TEST_IDS } from "./contracts"
import type { Submission } from "./contracts"


const submissions = [
  {
    test_id: "reactiontime",
    raw: { samples_ms: [200, 200, 200, 200, 200], too_soon_count: 0 },
    device: { input_kind: "mouse", viewport_width: 1280, viewport_height: 720, geometry: null },
    duration_ms: 1000,
    background_interrupted: false,
    submission_id: "00000000-0000-4000-8000-000000000001",
    engine_version: "reactiontime-clone-v1",
    locale: "zh-CN",
  },
  {
    test_id: "aim",
    raw: { intervals_ms: Array(30).fill(300) as number[], miss_count: 0, total_elapsed_ms: 9000 },
    device: {
      input_kind: "mouse",
      viewport_width: 1280,
      viewport_height: 720,
      geometry: { width: 1280, height: 720, target_diameter: 50 },
    },
    duration_ms: 9000,
    background_interrupted: false,
    submission_id: "00000000-0000-4000-8000-000000000002",
    engine_version: "aim-clone-v1",
    locale: "zh-CN",
  },
  {
    test_id: "sequence",
    raw: { reached_level: 1, completed_level: 0, failed_step: 1, end_reason: "wrong_tile" },
    device: { input_kind: "mouse", viewport_width: 1280, viewport_height: 720, geometry: null },
    duration_ms: 1,
    background_interrupted: false,
    submission_id: "00000000-0000-4000-8000-000000000003",
    engine_version: "sequence-clone-v1",
    locale: "zh-CN",
  },
  {
    test_id: "number-memory",
    raw: { correct_digits: 0, failed_digits: 1, display_duration_ms: 1800 },
    device: { input_kind: "mouse", viewport_width: 1280, viewport_height: 720, geometry: null },
    duration_ms: 1800,
    background_interrupted: false,
    submission_id: "00000000-0000-4000-8000-000000000004",
    engine_version: "number-memory-clone-v1",
    locale: "zh-CN",
  },
  {
    test_id: "verbal-memory",
    raw: { correct_count: 0, error_count: 3, total_count: 3, word_bank_version: "zh-192-v1" },
    device: { input_kind: "mouse", viewport_width: 1280, viewport_height: 720, geometry: null },
    duration_ms: 1,
    background_interrupted: false,
    submission_id: "00000000-0000-4000-8000-000000000005",
    engine_version: "verbal-memory-clone-v1",
    locale: "zh-CN",
  },
  {
    test_id: "chimp",
    raw: { reached_count: 4, completed_count: 0, strikes: 3, end_reason: "strikes" },
    device: { input_kind: "mouse", viewport_width: 1280, viewport_height: 720, geometry: null },
    duration_ms: 1,
    background_interrupted: false,
    submission_id: "00000000-0000-4000-8000-000000000006",
    engine_version: "chimp-clone-v1",
    locale: "zh-CN",
  },
  {
    test_id: "memory",
    raw: { reached_level: 1, completed_level: 0, accepted_misses: 9, lives_used: 3, end_reason: "lives" },
    device: { input_kind: "mouse", viewport_width: 1280, viewport_height: 720, geometry: null },
    duration_ms: 1,
    background_interrupted: false,
    submission_id: "00000000-0000-4000-8000-000000000007",
    engine_version: "memory-clone-v1",
    locale: "zh-CN",
  },
  {
    test_id: "dynamic-vision",
    raw: {
      selected_level: 4,
      outcomes: ["correct", "correct", "incorrect", "skipped", "correct"],
      measured_display_ms: [36, 36, 36, 36, 36],
      font_size_px: 56,
    },
    device: {
      input_kind: "mouse",
      viewport_width: 1280,
      viewport_height: 720,
      geometry: { width: 800, height: 420, target_diameter: null },
    },
    duration_ms: 13_000,
    background_interrupted: false,
    submission_id: "00000000-0000-4000-8000-000000000008",
    engine_version: "dynamic-vision-v1",
    locale: "zh-CN",
  },
] satisfies Submission[]


function acceptsSubmission(value: Submission): void {
  void value
}


describe("talent-test contracts", () => {
  it("keeps the catalog and explicit engine versions in the frozen eight-test order", () => {
    expect(TEST_IDS).toEqual([
      "reactiontime",
      "aim",
      "sequence",
      "number-memory",
      "verbal-memory",
      "chimp",
      "memory",
      "dynamic-vision",
    ])
    expect(ENGINE_VERSIONS).toEqual({
      reactiontime: "reactiontime-clone-v1",
      aim: "aim-clone-v1",
      sequence: "sequence-clone-v1",
      "number-memory": "number-memory-clone-v1",
      "verbal-memory": "verbal-memory-clone-v1",
      chimp: "chimp-clone-v1",
      memory: "memory-clone-v1",
      "dynamic-vision": "dynamic-vision-v2",
    })
    expect(submissions.map((submission) => submission.test_id)).toEqual(TEST_IDS)
  })

  it("binds each test discriminator to its own raw shape", () => {
    // @ts-expect-error reactiontime cannot submit the aim raw shape
    acceptsSubmission({ ...submissions[0], raw: submissions[1].raw })
    expect(submissions).toHaveLength(8)
  })
})
