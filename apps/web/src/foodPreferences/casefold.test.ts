import { describe, expect, it } from "vitest"

import { normalizeFoodTextKey } from "./casefold"

describe("normalizeFoodTextKey", () => {
  it.each([
    ["Straße", "strasse"],
    ["STRASSE", "strasse"],
    ["σ", "σ"],
    ["ς", "σ"],
    ["  Ｓｔｒａße\tBOWL  ", "strasse bowl"],
    ["川\u001c\u001c菜", "川 菜"],
    ["川\u0085菜", "川 菜"],
    ["\ufeff川菜\ufeff", "\ufeff川菜\ufeff"],
    ["\u{a7f1}", "\u{a7f1}"],
    ["\u{1ccd6}", "\u{1ccd6}"],
    ["\u0315\u0897", "\u0315\u0897"],
  ])("matches Python 3.12 normalization for %s", (value, expected) => {
    expect(normalizeFoodTextKey(value)).toBe(expected)
  })

  it("preserves distinctions that accent-insensitive collation would erase", () => {
    expect(normalizeFoodTextKey("café")).not.toBe(
      normalizeFoodTextKey("cafe"),
    )
    expect(normalizeFoodTextKey("ひらがな")).not.toBe(
      normalizeFoodTextKey("ヒラガナ"),
    )
  })
})
