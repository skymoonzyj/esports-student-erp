import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query"
import { act, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"
import { PersonForm } from "./PersonForm"

vi.mock("../auth/AuthProvider", () => ({ useOptionalAuth: () => ({ status: "authenticated", user: { account_id: "10000000-0000-4000-8000-000000000001", permissions: ["people.create"] } }) }))
afterEach(() => { vi.unstubAllGlobals(); focusManager.setFocused(undefined) })

it("offers the managed activation-role entry only after live management capability confirmation", async () => {
  let allowed = true
  const fetcher = vi.fn(async () => Response.json({ can_submit: false, can_review: false, can_read_managed: false, can_direct_create: allowed, can_direct_create_pending: allowed, allowed_team_ids: [], eligible_coaches: [], eligible_teams: [] }))
  vi.stubGlobal("fetch", fetcher)
  render(<MemoryRouter><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><PersonForm /></QueryClientProvider></MemoryRouter>)
  expect(await screen.findByRole("link", { name: "管理建档并设置激活角色" })).toHaveAttribute("href", "/people/assisted-create")
  expect(screen.getByRole("button", { name: "保存人员" })).toBeVisible()
  expect(screen.queryByRole("group", { name: "账号激活角色（激活后生效）" })).not.toBeInTheDocument()
  allowed = false
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await waitFor(() => expect(screen.queryByRole("link", { name: "管理建档并设置激活角色" })).not.toBeInTheDocument())
  expect(screen.getByRole("button", { name: "保存人员" })).toBeVisible()
  expect(fetcher.mock.calls.length).toBe(2)
})
