import { createHash, webcrypto } from "node:crypto"
import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import { describe, expect, it, vi } from "vitest"
import { canonicalJson, parseCommandEnvelope, parseResultEnvelope, parseResultReceipt, parseRunEvent, validateCommandForManifest, validateEventBatch, validateManifest, verifyManifest } from "./contracts"

const fixtures = JSON.parse(readFileSync(resolve(process.cwd(), "../../shared/reactionx/v1/fixtures.json"), "utf8"))
const digest = (value: unknown) => createHash("sha256").update(canonicalJson(value)).digest("hex")

describe("frozen ReactionX v1 shared contracts", () => {
  it("accepts all nine frozen modes, bound results, commands and receipt", () => {
    for (const entry of [...fixtures.cases, ...fixtures.timed_cases]) {
      const manifest = validateManifest(entry.manifest)
      const { manifest_digest: expected, ...body } = manifest
      expect(digest(body)).toBe(expected)
      expect(parseResultEnvelope(entry.result).run_id).toBe(manifest.run_id)
    }
    for (const command of Object.values(fixtures.commands)) parseCommandEnvelope(command)
    parseResultReceipt(fixtures.receipt)
  })
  it("matches byte-for-byte canonical JSON vectors and hashes", () => {
    for (const vector of fixtures.canonical_vectors) {
      expect(canonicalJson(vector.value)).toBe(vector.canonical_json)
      expect(digest(vector.value)).toBe(vector.sha256)
    }
  })
  it("rejects unknown fields, duplicated devices and invalid fixed task counts", () => {
    const original = fixtures.cases[1].manifest
    for (const change of [
      { ...original, rankable: true },
      { ...original, devices: [original.devices[0], original.devices[0]] },
      { ...original, config: { ...original.config, task_count: 29 } },
      { ...original, layout: { ...original.layout, positions: [] } },
    ]) expect(() => validateManifest(change)).toThrow()
  })
  it("rejects non-integer elapsed, client ranking flags and arbitrary command bytes", () => {
    const result = fixtures.cases[1].result
    for (const elapsed of [true, "1", 0.5, -1, 900001]) {
      expect(() => parseResultEnvelope({ ...result, events: [{ ...result.events[0], host_elapsed_ms: elapsed }] })).toThrow()
    }
    expect(() => parseResultEnvelope({ ...result, rankable: true })).toThrow()
    expect(() => parseCommandEnvelope({ ...fixtures.commands.scan, payload: { bytes: [1, 2] } })).toThrow()
    expect(() => canonicalJson({ a: 1.5 })).toThrow()
  })
  it("permits equal display labels but keeps opaque identity and manual mode binding", () => {
    const original = fixtures.cases[1].manifest
    expect(validateManifest({ ...original, devices: original.devices.map((d: { display_number: number }) => ({ ...d, display_number: 1 })) }).devices).toHaveLength(2)
    const command = parseCommandEnvelope(fixtures.commands.manual_activate)
    validateCommandForManifest(command, fixtures.cases[0].manifest)
    expect(() => validateCommandForManifest({ ...command, run_id: "fixture-standard" }, original)).toThrow()
  })
  it("preserves invalid evidence without inventing a successful device resolution", () => {
    const event = fixtures.cases[1].result.events[0]
    for (const quality_code of ["activation_ambiguous", "activation_write_failed", "unknown_activation", "duplicate_notification", "device_time_invalid", "event_limit_exceeded", "stale_connection", "stale_run", "scheduler_late"]) {
      expect(parseRunEvent({ ...event, kind: "invalid", quality_code }).quality_code).toBe(quality_code)
    }
    expect(() => parseRunEvent({ ...event, quality_code: "activation_ambiguous" })).toThrow()
    expect(() => validateEventBatch(Array(101).fill(event))).toThrow()
  })
  it("verifies real SHA-256 and rejects tampered manifests and impossible UTC dates", async () => {
    vi.stubGlobal("crypto", webcrypto)
    try {
      await expect(verifyManifest(fixtures.cases[1].manifest)).resolves.toEqual(fixtures.cases[1].manifest)
      await expect(verifyManifest({ ...fixtures.cases[1].manifest, manifest_digest: "0".repeat(64) })).rejects.toThrow()
    } finally { vi.unstubAllGlobals() }
    expect(() => parseCommandEnvelope({ ...fixtures.commands.scan, expires_at: "2026-99-31T25:00:00.000Z" })).toThrow()
  })
  it.each(["constructor", "toString", "__proto__"])("rejects own JSON prototype-named extra field %s", key => {
    const command = JSON.parse(JSON.stringify(fixtures.commands.scan).replace(/}$/, `,"${key}":{"unexpected":true}}`))
    expect(() => parseCommandEnvelope(command)).toThrow()
  })
  it.each([["0000", false], ["0001", true], ["9999", true]])("matches Python UTC year range for %s", (year, valid) => {
    const command = { ...fixtures.commands.scan, expires_at: `${year}-01-01T00:00:00.000Z` }
    if (valid) expect(parseCommandEnvelope(command).expires_at).toBe(command.expires_at)
    else expect(() => parseCommandEnvelope(command)).toThrow()
  })
})
