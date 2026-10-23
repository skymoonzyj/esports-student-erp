import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"
import { OrganizationManagersPanel } from "./OrganizationManagersPanel"

afterEach(() => vi.unstubAllGlobals())
const organizations = [{ id: "org-a", name: "邀请机构甲", is_active: true }, { id: "org-b", name: "邀请机构乙", is_active: false }]
it("hides manager controls without the independent superadmin gate", () => {
  render(<OrganizationManagersPanel accountId="actor" organizations={organizations} canManageManagers={false} />)
  expect(screen.queryByRole("button", { name: /查看负责人/ })).not.toBeInTheDocument()
})
it("opens the selected organization including disabled organizations", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify([]))))
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><OrganizationManagersPanel accountId="actor" organizations={organizations} canManageManagers /></QueryClientProvider>)
  await userEvent.setup().click(screen.getByRole("button", { name: "查看负责人：邀请机构乙" }))
  expect(screen.getByRole("dialog", { name: "邀请机构乙 · 负责人" })).toBeVisible()
  expect(screen.getByText(/停用机构不能新增负责人/)).toBeVisible()
})
