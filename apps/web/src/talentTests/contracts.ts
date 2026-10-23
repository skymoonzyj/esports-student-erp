export type TestId = "reactiontime" | "aim" | "sequence" | "number-memory"
  | "verbal-memory" | "chimp" | "memory" | "dynamic-vision"

export const TEST_IDS = [
  "reactiontime",
  "aim",
  "sequence",
  "number-memory",
  "verbal-memory",
  "chimp",
  "memory",
  "dynamic-vision",
] as const satisfies readonly TestId[]

export const ENGINE_VERSIONS = {
  reactiontime: "reactiontime-clone-v1",
  aim: "aim-clone-v1",
  sequence: "sequence-clone-v1",
  "number-memory": "number-memory-clone-v1",
  "verbal-memory": "verbal-memory-clone-v1",
  chimp: "chimp-clone-v1",
  memory: "memory-clone-v1",
  "dynamic-vision": "dynamic-vision-v1",
} as const satisfies Record<TestId, string>

export type InputKind = "mouse" | "touch" | "pen" | "mixed" | "unknown"
export type Grade = "low" | "medium" | "high" | "exceptional"
export type SessionStatus = "in_progress" | "completed" | "abandoned"

export type Geometry = {
  width: number
  height: number
  target_diameter: number | null
}

export type DeviceSnapshot = {
  input_kind: InputKind
  viewport_width: number
  viewport_height: number
  geometry: Geometry | null
}

export type RawByTest = {
  reactiontime: { samples_ms: number[]; too_soon_count: number }
  aim: { intervals_ms: number[]; miss_count: number; total_elapsed_ms: number }
  sequence: {
    reached_level: number
    completed_level: number
    failed_step: number
    end_reason: "wrong_tile"
  }
  "number-memory": {
    correct_digits: number
    failed_digits: number
    display_duration_ms: number
  }
  "verbal-memory": {
    correct_count: number
    error_count: number
    total_count: number
    word_bank_version: "zh-192-v1"
  }
  chimp: {
    reached_count: number
    completed_count: number
    strikes: number
    end_reason: "strikes" | "engine_cap"
  }
  memory: {
    reached_level: number
    completed_level: number
    accepted_misses: number
    lives_used: number
    end_reason: "lives"
  }
  "dynamic-vision": {
    selected_level: 1 | 2 | 3 | 4 | 5
    outcomes: [
      "correct" | "incorrect" | "skipped",
      "correct" | "incorrect" | "skipped",
      "correct" | "incorrect" | "skipped",
      "correct" | "incorrect" | "skipped",
      "correct" | "incorrect" | "skipped",
    ]
    measured_display_ms: [number, number, number, number, number]
    font_size_px: 40 | 56
  }
}

export type GameResult<K extends TestId> = {
  test_id: K
  raw: RawByTest[K]
  device: DeviceSnapshot
  duration_ms: number
  background_interrupted: boolean
}

export type CompletedGame = { [K in TestId]: GameResult<K> }[TestId]

export type Submission = { [K in TestId]: GameResult<K> & {
  submission_id: string
  engine_version: (typeof ENGINE_VERSIONS)[K]
  locale: "zh-CN"
}}[TestId]

export type GameProps<K extends TestId> = {
  onComplete: (result: GameResult<K>) => void
  onInterrupted: (reason: "hidden" | "geometry_changed") => void
}

export type TestRule = {
  engine_version: string
  anchors: [number, number, number, number, number, number]
  source_label: string
  unit: string
}

export type DynamicVisionLevel = {
  level: 1 | 2 | 3 | 4 | 5
  digit_count: 3 | 4 | 5 | 6
  nominal_display_ms: 36 | 100
  coefficient_tenths: 12 | 14 | 16 | 18 | 20
}

export type DynamicVisionRule = {
  kind: "dynamic_vision"
  engine_version: "dynamic-vision-v1"
  source_label: "内部试行训练规则"
  unit: "count"
  base_points_per_correct: 10
  levels: [
    DynamicVisionLevel,
    DynamicVisionLevel,
    DynamicVisionLevel,
    DynamicVisionLevel,
    DynamicVisionLevel,
  ]
}

export type RuleSnapshot = {
  catalog_version: string
  scoring_version: string
  report_version: string
  test_ids: TestId[]
  tests: Partial<Record<TestId, TestRule | DynamicVisionRule>>
}

export type NormalizedResult = {
  test_id: TestId
  raw_value: number
  unit: string
  score: number
  grade: Grade
  quality_flags: string[]
  raw: RawByTest[TestId]
  device: DeviceSnapshot
  duration_ms: number
  background_interrupted: boolean
  engine_version: string
  scoring_version: string
  locale: "zh-CN"
}

export type PersonDisplay = {
  id: string
  name: string
  nickname: string | null
}

export type ReportComparison = {
  baseline_report_id: string
  raw_delta: number
  score_delta: number
}

export type ReportSnapshot = {
  person: PersonDisplay
  session_id: string
  completed_at: string
  rules: RuleSnapshot
  results: NormalizedResult[]
  strengths: TestId[]
  suggestions: string[]
  comparisons: Partial<Record<TestId, ReportComparison>>
  mixed_devices: boolean
  disclaimer: string
}

export type ResultView = NormalizedResult & {
  id: string
  submission_id: string
  created_at: string
}

export type SessionView = {
  id: string
  person_id: string
  status: SessionStatus
  created_at: string
  completed_at: string | null
  abandoned_at: string | null
  rules: RuleSnapshot
  results: ResultView[]
  report_id: string | null
}

export type ResultSaveView = {
  session: SessionView
  result: ResultView
}

export type SessionSummary = {
  id: string
  status: SessionStatus
  created_at: string
  completed_at: string | null
  abandoned_at: string | null
  saved_count: number
  required_count: number
  report_id: string | null
}

export type ReportSummary = {
  id: string
  session_id: string
  person: PersonDisplay
  completed_at: string
  catalog_version: string
  scoring_version: string
}

export type ReportView = ReportSnapshot & { id: string }

export type Page<T> = {
  items: T[]
  total: number
  page: number
  page_size: number
}

export type ContextView = {
  catalog: RuleSnapshot
  can_take: boolean
  can_read_self: boolean
  can_read_reports: boolean
  active_session_id: string | null
}

export type SessionCreateRequest = {
  request_id: string
  catalog_version: string
}

export type AbandonRequest = { request_id: string }
