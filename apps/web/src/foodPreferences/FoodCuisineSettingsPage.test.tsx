import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import type { CuisineCatalog } from "./api"
import { FoodCuisineSettingsPage } from "./FoodCuisineSettingsPage"

const mocks = vi.hoisted(() => ({
  getFoodPreferenceCatalog: vi.fn(),
  createFoodCuisine: vi.fn(),
  patchFoodCuisine: vi.fn(),
}))

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal()),
  ...mocks,
}))

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({
    status: "authenticated",
    user: {
      account_id: "cuisine-admin",
      person_id: "10000000-0000-4000-8000-000000000001",
      full_name: "管理员",
      permissions: ["food_preferences.catalog.manage"],
      scoped_permissions: [],
    },
  }),
}))

const revision = "a".repeat(64)
const otherId = "20000000-0000-4000-8000-000000000099"
const sichuanId = "20000000-0000-4000-8000-000000000001"

function catalog(
  allowedActions: CuisineCatalog["allowed_actions"] = ["create", "update", "disable"],
): CuisineCatalog {
  return {
    items: [
      { id: sichuanId, name: "川菜", sort_order: 1, is_active: true, is_other: false },
      { id: otherId, name: "其他", sort_order: 99, is_active: true, is_other: true },
      { id: "20000000-0000-4000-8000-000000000002", name: "停用菜系", sort_order: 2, is_active: false, is_other: false },
    ],
    allowed_actions: allowedActions,
    access_revision: revision,
  }
}

function renderPage(onAccessRevoked = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(
    <MemoryRouter initialEntries={["/settings/food-cuisines"]}>
      <QueryClientProvider client={queryClient}>
        <FoodCuisineSettingsPage onAccessRevoked={onAccessRevoked} />
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return { queryClient, onAccessRevoked }
}

beforeEach(() => {
  mocks.getFoodPreferenceCatalog.mockReset().mockResolvedValue(catalog())
  mocks.createFoodCuisine.mockReset().mockResolvedValue({
    id: "20000000-0000-4000-8000-000000000003",
    name: "东北菜",
    sort_order: 3,
    is_active: true,
    is_other: false,
  })
  mocks.patchFoodCuisine.mockReset().mockImplementation(async (id: string, input: object) => ({
    ...catalog().items.find((item) => item.id === id),
    ...input,
  }))
})

it("groups active and inactive cuisines and protects Other controls", async () => {
  renderPage()
  expect(await screen.findByRole("heading", { name: "菜系字典管理" })).toBeVisible()
  expect(await screen.findByRole("region", { name: "启用菜系" })).toHaveTextContent("川菜")
  expect(await screen.findByRole("region", { name: "停用菜系" })).toHaveTextContent("停用菜系")
  const other = screen.getByRole("listitem", { name: /其他/ })
  expect(within(other).queryByRole("button", { name: /停用/ })).toBeNull()
  expect(within(other).queryByRole("button", { name: /编辑/ })).toBeNull()
})

it("creates a cuisine and supports disabling an ordinary cuisine", async () => {
  const user = userEvent.setup()
  renderPage()
  await screen.findByRole("button", { name: "新增菜系" })
  await user.click(screen.getByRole("button", { name: "新增菜系" }))
  const dialog = screen.getByRole("dialog", { name: "新增菜系" })
  await user.type(within(dialog).getByLabelText("菜系名称"), "东北菜")
  await user.clear(within(dialog).getByLabelText("排序"))
  await user.type(within(dialog).getByLabelText("排序"), "3")
  await user.click(within(dialog).getByRole("button", { name: "保存" }))
  await waitFor(() => expect(mocks.createFoodCuisine).toHaveBeenCalledWith({ name: "东北菜", sort_order: 3 }))

  const sichuan = screen.getByRole("listitem", { name: /川菜/ })
  await user.click(within(sichuan).getByRole("button", { name: "停用" }))
  await waitFor(() => expect(mocks.patchFoodCuisine).toHaveBeenCalledWith(sichuanId, { is_active: false }))
})

it.each([
  [["create"], true, false, false],
  [["update"], false, true, false],
  [["disable"], false, false, true],
] as const)(
  "renders create, edit, and status controls independently for %j",
  async (allowedActions, createVisible, editVisible, toggleVisible) => {
    mocks.getFoodPreferenceCatalog.mockResolvedValueOnce(
      catalog([...allowedActions]),
    )
    renderPage()

    await screen.findByRole("region", { name: "启用菜系" })
    const row = screen.getByRole("listitem", { name: /川菜/ })
    expect(screen.queryByRole("button", { name: "新增菜系" }) !== null).toBe(
      createVisible,
    )
    expect(within(row).queryByRole("button", { name: "编辑" }) !== null).toBe(
      editVisible,
    )
    expect(within(row).queryByRole("button", { name: "停用" }) !== null).toBe(
      toggleVisible,
    )
  },
)

it("shows a private access failure and delegates safe navigation", async () => {
  const onAccessRevoked = vi.fn()
  mocks.getFoodPreferenceCatalog.mockRejectedValueOnce(new ProblemDetailsError({
    type: "about:blank", title: "Forbidden", status: 403, detail: "禁止访问", code: "permission_denied", request_id: "private",
  }))
  renderPage(onAccessRevoked)
  await waitFor(() => expect(onAccessRevoked).toHaveBeenCalledTimes(1))
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
})

it("closes the editor before delegating a private create failure", async () => {
  const onAccessRevoked = vi.fn(() => {
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  })
  mocks.createFoodCuisine.mockRejectedValueOnce(new ProblemDetailsError({
    type: "about:blank", title: "Not found", status: 404, detail: "不存在", code: "not_found", request_id: "private-create",
  }))
  renderPage(onAccessRevoked)
  const user = userEvent.setup()

  await user.click(await screen.findByRole("button", { name: "新增菜系" }))
  await user.type(screen.getByLabelText("菜系名称"), "东北菜")
  await user.clear(screen.getByLabelText("排序"))
  await user.type(screen.getByLabelText("排序"), "3")
  await user.click(screen.getByRole("button", { name: "保存" }))

  await waitFor(() => expect(onAccessRevoked).toHaveBeenCalledTimes(1))
})
