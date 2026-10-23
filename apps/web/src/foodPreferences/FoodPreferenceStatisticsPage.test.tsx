import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import type { FoodPreferenceStatistics } from "./api"
import { FoodPreferenceStatisticsPage } from "./FoodPreferenceStatisticsPage"

const apiMocks = vi.hoisted(() => ({
  getFoodPreferenceStatistics: vi.fn(),
  exportFoodPreferences: vi.fn(),
}))

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal()),
  ...apiMocks,
}))

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({
    status: "authenticated",
    user: {
      account_id: "account-food-admin",
      person_id: "10000000-0000-4000-8000-000000000001",
      full_name: "餐饮管理员",
      email: "food-admin@example.test",
      force_password_change: false,
      permissions: ["food_preferences.export"],
      scoped_permissions: [],
    },
  }),
}))

const revision = "a".repeat(64)
const personId = "20000000-0000-4000-8000-000000000001"

function statistics(overrides: Partial<FoodPreferenceStatistics> = {}): FoodPreferenceStatistics {
  return {
    population: "students",
    include_inactive: false,
    total_people: 3,
    filled_people: 2,
    unfilled_people: 1,
    filled_ratio: 2 / 3,
    cuisines: [
      { label: "川菜", count: 2, ratio: 2 / 3 },
      { label: "粤菜", count: 1, ratio: 1 / 3 },
    ],
    dishes: [{ label: "鱼香肉丝", count: 2, ratio: 2 / 3 }],
    people: {
      items: [{
        person_id: personId,
        full_name: "张三",
        person_types: ["student"],
        status: "active",
        cuisines: ["川菜"],
        dishes: ["鱼香肉丝"],
        updated_at: "2026-08-12T10:00:00+08:00",
      }],
      page: 1,
      page_size: 20,
      total: 3,
    },
    access_revision: revision,
    ...overrides,
  }
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <MemoryRouter initialEntries={["/people/food-preferences"]}>
      <QueryClientProvider client={client}>
        <FoodPreferenceStatisticsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return client
}

beforeEach(() => {
  apiMocks.getFoodPreferenceStatistics.mockReset()
  apiMocks.exportFoodPreferences.mockReset()
  apiMocks.getFoodPreferenceStatistics.mockResolvedValue(statistics())
  apiMocks.exportFoodPreferences.mockResolvedValue({
    blob: new Blob(["food report"]),
    filename: "food-preferences-students.xlsx",
  })
})

it("renders summary, ranked preferences, and people details", async () => {
  renderPage()

  expect(await screen.findByRole("heading", { name: "餐饮偏好统计" })).toBeVisible()
  const summary = await screen.findByRole("region", { name: "餐饮偏好汇总" })
  expect(within(summary).getByText("填报人数").parentElement).toHaveTextContent("2")
  expect(within(summary).getByText("填报率").parentElement).toHaveTextContent("66.7%")
  expect(screen.getByRole("table", { name: "人员餐饮偏好明细" })).toBeVisible()
  expect(screen.getAllByText("川菜").length).toBeGreaterThan(0)
  expect(screen.getAllByText("鱼香肉丝").length).toBeGreaterThan(0)
})

it("switches population and includes inactive people while resetting page", async () => {
  const user = userEvent.setup()
  renderPage()

  await screen.findByRole("heading", { name: "餐饮偏好统计" })
  await user.selectOptions(screen.getByLabelText("统计范围"), "all_people")
  await user.click(screen.getByLabelText("包含停用或历史人员"))

  await waitFor(() => expect(apiMocks.getFoodPreferenceStatistics).toHaveBeenLastCalledWith(
    expect.objectContaining({ population: "all_people", include_inactive: true, page: 1 }),
    expect.anything(),
  ))
})

it("shows loading and retryable errors", async () => {
  apiMocks.getFoodPreferenceStatistics.mockRejectedValueOnce(new Error("network"))
  renderPage()

  expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法读取餐饮偏好统计")
  apiMocks.getFoodPreferenceStatistics.mockResolvedValueOnce(statistics())
  await userEvent.setup().click(screen.getByRole("button", { name: "重试" }))
  expect(await screen.findByRole("heading", { name: "餐饮偏好统计" })).toBeVisible()
})

it("validates export purpose and downloads a selected format", async () => {
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole("button", { name: "导出统计" }))
  const dialog = screen.getByRole("dialog", { name: "导出餐饮偏好统计" })
  expect(within(dialog).getByRole("button", { name: "确认导出" })).toBeDisabled()
  await user.type(within(dialog).getByLabelText("导出用途"), "安排下一期营地餐食")
  await user.selectOptions(within(dialog).getByLabelText("文件格式"), "csv_zip")
  await user.click(within(dialog).getByRole("button", { name: "确认导出" }))

  await waitFor(() => expect(apiMocks.exportFoodPreferences).toHaveBeenCalledWith({
    population: "students",
    include_inactive: false,
    format: "csv_zip",
    purpose: "安排下一期营地餐食",
  }))
  expect(await screen.findByRole("status")).toHaveTextContent("已下载")
})

it("returns a safe private message when access is revoked", async () => {
  apiMocks.getFoodPreferenceStatistics.mockRejectedValueOnce(new ProblemDetailsError({
    type: "about:blank",
    title: "Forbidden",
    status: 403,
    detail: "禁止访问",
    code: "permission_denied",
    request_id: "request-private",
  }))
  renderPage()
  expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法读取餐饮偏好统计")
})
