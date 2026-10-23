import { describe, expect, it } from "vitest"

import { containsKnownPrivateValue } from "./talentStoragePrivacy"

const knownPrivateValues = new Set(["00000", "000000"])

describe("talent sessionStorage privacy oracle", () => {
  it("rejects a generic primitive and nested known synthetic value", () => {
    expect(containsKnownPrivateValue({ lastValue: "00000" }, knownPrivateValues)).toBe(true)
    expect(
      containsKnownPrivateValue(
        { safeName: { values: ["public", "000000"] } },
        knownPrivateValues,
      ),
    ).toBe(true)
  })

  it("allows legitimate scores, identifiers, and non-exact substrings", () => {
    expect(
      containsKnownPrivateValue(
        {
          score: 90,
          submission_id: "00000000-0000-4000-8000-000000000003",
          note: "prefix-00000-suffix",
        },
        knownPrivateValues,
      ),
    ).toBe(false)
  })
})
