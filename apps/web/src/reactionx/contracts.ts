/** Frozen ReactionX v1. Wire schema is shared with Python/native consumers.
 * Schema validation is structural; validateManifest and event checks below add
 * cross-field invariants. The ERP alone decides score/eligibility/rank.
 */
import schemaBundle from "../../../../shared/reactionx/v1/schema.json"

export type Mode = "manual" | "standard" | "sequence" | "simultaneous" | "base" | "auditory" | "true_false" | "counter" | "whack"
export type EndKind = "task_completed" | "time_completed" | "all_lit" | "user_stopped" | "lease_expired" | "disconnected" | "process_interrupted" | "device_error" | "storage_error"
export type EventKind = "activated" | "hit" | "timeout" | "ignored" | "group_started" | "group_completed" | "cycle_started" | "cycle_completed" | "invalid"
export type StimulusRole = "target" | "distractor" | "base"
export type QualityCode = "activation_ambiguous" | "activation_write_failed" | "unknown_activation" | "duplicate_notification" | "device_time_invalid" | "event_limit_exceeded" | "stale_connection" | "stale_run" | "scheduler_late"
export interface DeviceDefinition { device_key: string; display_number: number; protocol_version: 3 | 4; order: number }
export interface LayoutPosition { device_key: string; x_cm: number; y_cm: number; z_cm: number }
export interface RunLayout { posture: "standing" | "seated" | "custom" | "unregistered"; positions: LayoutPosition[] }
export interface RunConfig {
  lamp_count: number; sensor: number; flash: number; on_sound: boolean; off_sound: boolean; color: number
  stimulus_interval_ms: number; timeout_ms: number; restart_interval_ms: number | null
  duration_ms: number | null; task_count: number | null; seed: number
  schedule_algorithm: "balanced_cycle_v1"; base_device_key: string | null
  target_color: number | null; distractor_color: number | null
}
export interface RunManifest {
  protocol_version: 1; run_id: string; actor_account_id: string; subject_person_id: string
  connector_id: string; rule_version: "reactionx-v1"; mode: Mode; config: RunConfig
  devices: DeviceDefinition[]; layout: RunLayout; manifest_digest: string
}
export interface RunEvent {
  seq: number; activation_id: string | null; device_key: string | null; kind: EventKind
  host_elapsed_ms: number; device_ticks_10ms: number | null
  round: number | null; group: number | null; cycle: number | null
  stimulus_role: StimulusRole | null; color: number | null
  termination_reason: "host_timeout" | "hardware_timeout" | null
  quality_code: QualityCode | null
}
export interface RunEnd { kind: EndKind; active_elapsed_ms: number }
export interface ResultEnvelope {
  protocol_version: 1; engine_version: "reactionx-engine-v1"; run_id: string
  manifest_digest: string; events: RunEvent[]; end: RunEnd
}
export interface ResultReceipt { run_id: string; result_id: string; digest: string; saved_at: string }
interface CommandBase {
  protocol_version: 1; operation_id: string; actor_account_id: string; connector_id: string
  sequence: number; expires_at: string; lease_remaining_ms: number
}
type Unbound = { run_id: null; subject_person_id: null }
type Bound = { run_id: string; subject_person_id: string }
export type CommandEnvelope = CommandBase & (
  | (Unbound & { kind: "scan"; payload: { duration_ms: number } })
  | (Unbound & { kind: "identify"; payload: { device_key: string; color: number; duration_ms: number } })
  | (Unbound & { kind: "connect" | "disconnect"; payload: { device_keys: string[] } })
  | (Bound & { kind: "prepare"; payload: { manifest: RunManifest } })
  | (Bound & { kind: "start"; payload: { manifest_digest: string } })
  | (Bound & { kind: "stop"; payload: { reason: "user_stopped" | "lease_expired" | "disconnected" } })
  | (Bound & { kind: "manual_activate"; payload: { device_key: string; color: number } })
)
export interface ExactMetric { key: string; direction: "asc" | "desc"; unit: "ms" | "count"; numerator: number; denominator: number }
export interface MetricResult { eligible: boolean; reason: string | null; values: Record<string, number | null>; comparison_key: string; dimensions: ExactMetric[] }
export interface RuleDefinition {
  version: "reactionx-v1"; mode: Mode; task_count: number | null; duration_ms: number | null
  allowed_ends: EndKind[]; metric_keys: string[]; verification_status: "unverified"
}

type SchemaNode = {
  $ref?: string; anyOf?: SchemaNode[]; const?: unknown; enum?: unknown[]; type?: string
  minimum?: number; maximum?: number; multipleOf?: number; pattern?: string
  minItems?: number; maxItems?: number; items?: SchemaNode
  required?: string[]; properties?: Record<string, SchemaNode>; additionalProperties?: boolean
}
const definitions = schemaBundle.$defs as unknown as Record<string, SchemaNode>
function ensure(condition: unknown, message = "invalid ReactionX v1 contract"): asserts condition {
  if (!condition) throw new Error(message)
}

/** Deliberately supports only the JSON Schema vocabulary emitted by v1. */
function matches(value: unknown, schema: SchemaNode): boolean {
  if (schema.$ref) return matches(value, definitions[schema.$ref.split("/").at(-1)!])
  if (schema.anyOf) return schema.anyOf.some(option => matches(value, option))
  if ("const" in schema && value !== schema.const) return false
  if (schema.enum && !schema.enum.includes(value)) return false
  switch (schema.type) {
    case "null": return value === null
    case "integer": return typeof value === "number" && Number.isSafeInteger(value)
      && (schema.minimum === undefined || value >= schema.minimum)
      && (schema.maximum === undefined || value <= schema.maximum)
      && (schema.multipleOf === undefined || value % schema.multipleOf === 0)
    case "boolean": return typeof value === "boolean"
    case "string": return typeof value === "string" && (!schema.pattern || new RegExp(schema.pattern).test(value))
    case "array": return Array.isArray(value)
      && (schema.minItems === undefined || value.length >= schema.minItems)
      && (schema.maxItems === undefined || value.length <= schema.maxItems)
      && value.every(item => !schema.items || matches(item, schema.items))
    case "object": {
      if (!value || typeof value !== "object" || Array.isArray(value)) return false
      const object = value as Record<string, unknown>
      const properties = schema.properties ?? {}
      return (schema.required ?? []).every(key => Object.hasOwn(object, key))
        && Object.entries(object).every(([key, item]) => Object.hasOwn(properties, key)
          ? matches(item, properties[key]) : schema.additionalProperties !== false)
    }
    default: return true
  }
}

function parse<T>(name: string, value: unknown): T {
  ensure(matches(value, definitions[name]), `invalid ${name}`)
  // Catch non-JSON types and unsafe numbers even outside a modeled leaf.
  canonicalJson(value)
  return value as T
}

export function canonicalJson(value: unknown): string {
  if (value === null || typeof value === "boolean") return JSON.stringify(value)
  if (typeof value === "string") {
    for (let i = 0; i < value.length; i++) {
      const code = value.charCodeAt(i)
      if (code >= 0xd800 && code <= 0xdbff) {
        const next = value.charCodeAt(++i)
        ensure(next >= 0xdc00 && next <= 0xdfff, "unpaired Unicode surrogate")
      } else ensure(code < 0xdc00 || code > 0xdfff, "unpaired Unicode surrogate")
    }
    return JSON.stringify(value)
  }
  if (typeof value === "number") {
    ensure(Number.isSafeInteger(value), "wire JSON requires safe integers")
    return JSON.stringify(value)
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`
  ensure(typeof value === "object" && value !== null && Object.getPrototypeOf(value) === Object.prototype, "wire JSON requires plain objects")
  return `{${Object.keys(value).sort().map(key => `${canonicalJson(key)}:${canonicalJson((value as Record<string, unknown>)[key])}`).join(",")}}`
}

export async function canonicalDigest(value: unknown): Promise<string> {
  const data = new TextEncoder().encode(canonicalJson(value))
  const hash = await globalThis.crypto.subtle.digest("SHA-256", data)
  return Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, "0")).join("")
}

/** Structural and cross-field validation; use verifyManifest for digest verification. */
export function validateManifest(value: unknown): RunManifest {
  const manifest = parse<RunManifest>("RunManifest", value)
  const { config: c, devices, layout, mode } = manifest
  const keys = new Set(devices.map(d => d.device_key))
  ensure(keys.size === devices.length)
  ensure(devices.every((d, i) => d.order === i) && c.lamp_count === devices.length)
  const layoutKeys = new Set(layout.positions.map(p => p.device_key))
  ensure(layoutKeys.size === layout.positions.length && [...layoutKeys].every(key => keys.has(key)))
  if (devices.some(d => d.protocol_version === 3)) ensure(c.sensor < 4 && c.flash < 2 && ![c.color, c.target_color, c.distractor_color].includes(8))
  if (mode === "manual") return manifest
  ensure(layout.posture !== "unregistered" && layoutKeys.size === keys.size)
  ensure(c.stimulus_interval_ms >= 10)
  const taskCounts: Partial<Record<Mode, number>> = { standard: 30, sequence: 5, simultaneous: 5, base: 10, auditory: 30, true_false: 30 }
  ensure(c.task_count === (taskCounts[mode] ?? null))
  ensure(c.duration_ms === (mode === "counter" ? 30000 : mode === "whack" ? 60000 : null))
  if (["sequence", "simultaneous", "base", "whack"].includes(mode)) ensure(devices.length >= 2)
  ensure(mode === "counter" ? c.restart_interval_ms !== null && c.restart_interval_ms >= 10 : c.restart_interval_ms === null)
  ensure(mode === "base" ? c.base_device_key !== null && keys.has(c.base_device_key) : c.base_device_key === null)
  if (mode === "true_false") ensure(c.target_color !== null && c.distractor_color !== null && c.target_color > 0 && c.distractor_color > 0 && c.target_color !== c.distractor_color)
  else ensure(c.target_color === null && c.distractor_color === null)
  ensure(mode === "auditory" ? c.color === 0 && c.on_sound && c.flash === 0 : c.color !== 0)
  return manifest
}

export async function verifyManifest(value: unknown): Promise<RunManifest> {
  const manifest = validateManifest(value)
  const { manifest_digest, ...body } = manifest
  ensure(await canonicalDigest(body) === manifest_digest, "manifest digest mismatch")
  return manifest
}

export function parseRunEvent(value: unknown): RunEvent {
  const event = parse<RunEvent>("RunEvent", value)
  if (event.kind === "invalid") {
    ensure(event.quality_code !== null && event.device_ticks_10ms === null && event.termination_reason === null)
    return event
  }
  ensure(event.quality_code === null)
  const boundary = event.kind.startsWith("group_") || event.kind.startsWith("cycle_")
  if (boundary) {
    ensure([event.activation_id, event.device_key, event.stimulus_role, event.color, event.device_ticks_10ms, event.termination_reason, event.round].every(v => v === null))
    ensure(event.kind.startsWith("group_") ? event.group !== null && event.cycle === null : event.cycle !== null && event.group === null)
  } else {
    ensure([event.activation_id, event.device_key, event.stimulus_role, event.color].every(v => v !== null))
    ensure(event.kind === "hit" ? event.device_ticks_10ms !== null && event.termination_reason === null : event.device_ticks_10ms === null)
    ensure(["timeout", "ignored"].includes(event.kind) === (event.termination_reason !== null))
  }
  return event
}

export function parseResultEnvelope(value: unknown): ResultEnvelope {
  const result = parse<ResultEnvelope>("ResultEnvelope", value)
  result.events.forEach(parseRunEvent)
  ensure(new TextEncoder().encode(canonicalJson(value)).length <= 8 * 1024 * 1024, "result exceeds 8 MiB")
  return result
}

export function parseResultReceipt(value: unknown): ResultReceipt {
  const receipt = parse<ResultReceipt>("ResultReceipt", value)
  validateUTCStamp(receipt.saved_at)
  return receipt
}

function validateUTCStamp(value: string): void {
  const date = new Date(value)
  ensure(Number.isFinite(date.valueOf()) && date.getUTCFullYear() >= 1 && date.getUTCFullYear() <= 9999
    && date.toISOString() === value, "invalid UTC timestamp")
}

export function parseCommandEnvelope(value: unknown): CommandEnvelope {
  const command = parse<CommandEnvelope>("CommandEnvelope", value)
  validateUTCStamp(command.expires_at)
  const names = { scan: "ScanPayload", identify: "IdentifyPayload", connect: "DeviceKeysPayload", disconnect: "DeviceKeysPayload", prepare: "PreparePayload", start: "StartPayload", stop: "StopPayload", manual_activate: "ManualActivatePayload" }
  ensure(matches(command.payload, definitions[names[command.kind]]))
  const bound = ["prepare", "start", "stop", "manual_activate"].includes(command.kind)
  ensure(bound ? command.run_id !== null && command.subject_person_id !== null : command.run_id === null && command.subject_person_id === null)
  if (command.kind === "connect" || command.kind === "disconnect") ensure(new Set(command.payload.device_keys).size === command.payload.device_keys.length)
  if (command.kind === "prepare") {
    const manifest = validateManifest(command.payload.manifest)
    ensure(command.run_id === manifest.run_id && command.subject_person_id === manifest.subject_person_id
      && command.actor_account_id === manifest.actor_account_id && command.connector_id === manifest.connector_id)
  }
  return command
}

export function validateCommandForManifest(value: unknown, storedManifest: unknown): void {
  const command = parseCommandEnvelope(value)
  const manifest = validateManifest(storedManifest)
  ensure(command.run_id === manifest.run_id && command.subject_person_id === manifest.subject_person_id
    && command.actor_account_id === manifest.actor_account_id && command.connector_id === manifest.connector_id)
  if (command.kind === "start") ensure(command.payload.manifest_digest === manifest.manifest_digest)
  if (command.kind === "manual_activate") {
    ensure(manifest.mode === "manual")
    const device = manifest.devices.find(d => d.device_key === command.payload.device_key)
    ensure(device && !(command.payload.color === 8 && device.protocol_version === 3))
  }
}

export function validateEventBatch(value: unknown): RunEvent[] {
  ensure(Array.isArray(value) && value.length <= 100, "event batch exceeds 100 events")
  const events = value.map(parseRunEvent)
  ensure(new TextEncoder().encode(canonicalJson(value)).length <= 256 * 1024, "event batch exceeds 256 KiB")
  return events
}
