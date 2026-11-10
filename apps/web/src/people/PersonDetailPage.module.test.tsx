import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen } from "@testing-library/react"
import { useState } from "react"
import { Link, MemoryRouter, Route, Routes } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { PersonDetailPage } from "./PersonDetailPage"

const context = vi.hoisted(() => ({ account: "account-a", failed: true }))
vi.mock("../auth/AuthProvider", () => ({
  useOptionalAuth: () => ({ status: "authenticated", user: { account_id: context.account, permissions: [], scoped_permissions: [] } }),
}))
vi.mock("../esportsAssessments/AssessmentReportsPanel", () => ({
  AssessmentReportsPanel: ({ personId }: { personId: string }) => {
    if (context.failed) throw new Error("report-render-failure")
    return <p>已恢复报告 {personId}</p>
  },
}))

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

it.each(["person", "account", "role"])("does not retain a module failure after a %s scope change", async change => {
  context.account = "account-a"
  context.failed = true
  vi.spyOn(console, "error").mockImplementation(() => undefined)
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async input => {
    const id = String(input).split("/").at(-1)
    return Response.json({
      id, full_name: `合成档案 ${id}`, person_types: [], nickname: null,
      identity_number: null, phone: null, email: null, sex: null,
      date_of_birth: null, created_at: "2026-10-25T00:00:00Z", age_status: "unknown",
      guardian_status: "not_applicable", student_number: null, student_status: null,
      staff_type: null, visit_reason: null, linked_student_id: null,
    })
  }))
  function SwitchingProfile() {
    const [, rerender] = useState(0)
    const [roleChanged, setRoleChanged] = useState(false)
    return <>
      <Link to="/people/person-b" onClick={() => { context.failed = false }}>切换人员</Link>
      <button onClick={() => { context.failed = false; context.account = "account-b"; rerender(value => value + 1) }}>切换账号</button>
      <button onClick={() => { context.failed = false; setRoleChanged(true) }}>切换角色</button>
      <Routes><Route path="/people/:personId" element={<PersonDetailPage permissions={new Set(["people.read", "esports_assessments.read", ...(roleChanged ? ["people.phone.reveal"] : [])])} />} /></Routes>
    </>
  }
  render(<MemoryRouter initialEntries={["/people/person-a"]}><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><SwitchingProfile /></QueryClientProvider></MemoryRouter>)
  expect(await screen.findByRole("alert")).toHaveTextContent("天赋评测报告暂时无法加载")
  if (change === "person") fireEvent.click(screen.getByRole("link", { name: "切换人员" }))
  else fireEvent.click(screen.getByRole("button", { name: change === "account" ? "切换账号" : "切换角色" }))
  expect(await screen.findByText(`已恢复报告 ${change === "person" ? "person-b" : "person-a"}`)).toBeVisible()
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
})
