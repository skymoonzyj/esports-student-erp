import { expect, it } from "vitest"
import { personTypeText } from "./presentation"

it("labels approved profile identities without fabricating account roles or repeating player", () => {
  expect(personTypeText([], null, [], "player")).toBe("队员")
  expect(personTypeText(["student"], null, [{ key: "team_member", name: "队员" }], "both")).toBe("学员 · 队员")
  expect(personTypeText([], null, [], "both")).toBe("学员 · 队员")
  expect(personTypeText(["staff"], "coach", [], null)).toBe("工作人员 · 教练")
})
