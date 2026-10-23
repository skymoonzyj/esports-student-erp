import { expect, it } from "vitest"
import { ageText, personTypeText, teamAllocationText } from "./presentation"

it("labels approved profile identities without fabricating account roles or repeating player", () => {
  expect(personTypeText([], null, [], "player")).toBe("队员")
  expect(personTypeText(["student"], null, [{ key: "team_member", name: "队员" }], "both")).toBe("学员 · 队员")
  expect(personTypeText([], null, [], "both")).toBe("学员 · 队员")
  expect(personTypeText(["staff"], "coach", [], null)).toBe("工作人员 · 教练")
  expect(personTypeText([], "other", [], "staff")).toBe("工作人员")
})

it("does not infer hidden or historical team allocation as pending", () => {
  expect(teamAllocationText({ visibility: "allowed", status: "pending", version: 1, can_update: false })).toBe("待划分队伍")
  expect(teamAllocationText({ visibility: "allowed", status: "assigned", version: 2, can_update: false })).toBe("已划分队伍")
  expect(teamAllocationText({ visibility: "allowed", status: "not_required", version: 2, can_update: false })).toBe("无需分队")
  expect(teamAllocationText({ visibility: "allowed", status: "untracked", version: 1, can_update: false })).toBe("未纳入分队待办")
  expect(teamAllocationText({ visibility: "denied", status: null, version: null, can_update: false })).toBe("无查看权限")
  expect(teamAllocationText(undefined)).toBe("无查看权限")
})

it("shows missing birth date as unknown rather than adult or zero years", () => {
  expect(ageText(null)).toBe("年龄待补")
})
