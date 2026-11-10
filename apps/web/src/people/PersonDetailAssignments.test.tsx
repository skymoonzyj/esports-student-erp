import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { PersonDetailPage } from "./PersonDetailPage"

vi.mock("../auth/AuthProvider", () => ({
  useOptionalAuth: () => ({ status: "authenticated", user: { account_id: "actor", permissions: ["people.read"], scoped_permissions: [] }, refreshUser: async () => undefined }),
}))

afterEach(() => vi.unstubAllGlobals())

it("opens profile assignments on demand without loading editor data for a closed panel", async () => {
  const requests: string[] = []
  const response = (body: unknown) => new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } })
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const path = String(input)
    requests.push(path)
    if (path.endsWith("/assignments")) return response({
      person_id: "person", version: "one", business_kind: null, source_intake_kind: null,
      student_project_ids: [], coach_person_ids: [],
      current_projects: [], current_coaches: [], eligible_projects: [], eligible_coaches: [],
      capabilities: { can_edit: false, can_manage_account_roles: false, can_remove_identity: false },
      account_id: null, account_status: "not_activated",
    })
    if (path === "/api/v1/people/person") return response({
      id: "person", full_name: "合成测试人员", nickname: null, person_types: ["staff"],
      identity_number: "********", phone: null, email: null, sex: "male", date_of_birth: null,
      staff_type: "other", created_at: "2026-10-25T00:00:00Z", age_status: "unknown",
      guardian_status: "not_applicable", assisted_kind: null, account_status: "not_activated",
      student_number: null, student_status: null, visit_reason: null, linked_student_id: null,
      team: { visibility: "allowed", current: null },
    })
    throw new Error(`Unexpected request ${path}`)
  }))
  render(<MemoryRouter initialEntries={["/people/person"]}><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Routes><Route path="/people/:personId" element={<PersonDetailPage permissions={new Set(["people.read"])} />} /></Routes></QueryClientProvider></MemoryRouter>)
  await screen.findByRole("heading", { name: "合成测试人员" })
  expect(requests).toEqual(["/api/v1/people/person"])
  const toggle = screen.getByRole("button", { name: "身份与归属" })
  expect(toggle).toHaveAttribute("aria-expanded", "false")
  fireEvent.click(toggle)
  await waitFor(() => expect(requests).toContain("/api/v1/people/person/assignments"))
  expect(toggle).toHaveAttribute("aria-expanded", "true")
  fireEvent.click(toggle)
  expect(toggle).toHaveAttribute("aria-expanded", "false")
  expect(screen.queryByText("保存身份与归属")).not.toBeInTheDocument()
})
