import { expect, it } from "vitest"
import { documentNumberLabel, documentTypeLabel, normalizeHkMoPermit } from "./documentFacts"

it.each([
  ["H12345678", "H12345678"], [" m1234567802 ", "M12345678"], ["h1234567800", "H12345678"],
  ["H1234567", null], ["H123456789", null], ["H123456789012", null], ["H1234 5678", null],
  ["Ｈ12345678", null], ["H１２３４５６７８", null], ["A12345678", null], ["HA12345678", null],
])("normalizes only ASCII return permit lifetime identity %s", (value, expected) => {
  expect(normalizeHkMoPermit(value!)).toBe(expected)
})

it("labels return permits distinctly from HKID and passports", () => {
  expect(documentTypeLabel("hk_mo_permit")).toBe("回乡证（港澳居民来往内地通行证）")
  expect(documentNumberLabel("hk_mo_permit")).toBe("回乡证号码")
})
