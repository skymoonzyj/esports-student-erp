import { fireEvent, render, screen } from "@testing-library/react"
import { expect, it, vi } from "vitest"
import { ActivationRolePicker } from "./ActivationRolePicker"

it("renders all thirteen backend catalog roles without introducing a client-side role allowlist", () => {
  const roles = [
    ["assistant_coach", "助教"], ["property", "物业"], ["coach", "教练"], ["customer_service", "客服"],
    ["life_teacher", "生活老师"], ["staff", "其他员工"], ["guardian", "监护人"], ["student", "学员"],
    ["registered_user", "注册用户"], ["administration", "行政"], ["finance", "财务"], ["team_member", "队员"], ["team_manager", "队伍管理"],
  ]
  const change = vi.fn()
  render(<ActivationRolePicker options={{ items: roles.map(([key, name]) => ({ key, name, assignable: true, reason: null })) }} selected={[]} onChange={change} />)
  expect(screen.getAllByRole("checkbox")).toHaveLength(13)
  for (const label of ["助教", "物业", "教练", "客服", "生活老师", "其他员工", "监护人", "学员", "注册用户", "行政", "财务", "队员", "队伍管理"]) {
    expect(screen.getByRole("checkbox", { name: label })).toBeEnabled()
  }
  fireEvent.click(screen.getByRole("checkbox", { name: "财务" }))
  expect(change).toHaveBeenCalledExactlyOnceWith(["finance"])
  expect(screen.queryByRole("checkbox", { name: "管理员" })).not.toBeInTheDocument()
  expect(screen.queryByRole("checkbox", { name: "超级管理员" })).not.toBeInTheDocument()
})

it("keeps an unavailable selection visible and removable rather than silently granting or dropping it", () => {
  const change = vi.fn()
  render(<ActivationRolePicker options={{ items: [{ key: "coach", name: "教练", assignable: false, reason: "权限范围不足" }] }} selected={["coach", "removed_role"]} onChange={change} />)
  expect(screen.getByRole("checkbox", { name: "教练" })).toBeChecked()
  expect(screen.getByRole("checkbox", { name: "教练" })).toBeDisabled()
  expect(screen.getByRole("checkbox", { name: "教练" })).toHaveAccessibleDescription("权限范围不足")
  fireEvent.click(screen.getByRole("button", { name: "移除不可用角色 2" }))
  expect(change).toHaveBeenCalledExactlyOnceWith(["coach"])
})
