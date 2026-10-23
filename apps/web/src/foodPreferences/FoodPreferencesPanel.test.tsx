import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { readFileSync } from "node:fs"
import { useState } from "react"
import { MemoryRouter, useNavigate } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import type {
  CuisineCatalog,
  FoodPreferenceDraft,
  FoodPreferenceProfile,
  FoodPreferenceSelfResponse,
} from "./api"
import { FoodPreferencesPanel } from "./FoodPreferencesPanel"
import { catalog, foodPreferenceKeys, person, revisionRoot } from "./keys"

const accountId = "account-food-a"
const otherAccountId = "account-food-b"
const personId = "10000000-0000-4000-8000-000000000001"
const otherPersonId = "10000000-0000-4000-8000-000000000002"
const revisionA = "a".repeat(64)
const revisionB = "b".repeat(64)
const cuisineIds = {
  sichuan: "20000000-0000-4000-8000-000000000001",
  cantonese: "20000000-0000-4000-8000-000000000002",
  hunan: "20000000-0000-4000-8000-000000000003",
  shandong: "20000000-0000-4000-8000-000000000004",
  other: "20000000-0000-4000-8000-000000000005",
  retired: "20000000-0000-4000-8000-000000000006",
}

const apiMocks = vi.hoisted(() => ({
  getFoodPreferenceCatalog: vi.fn(),
  getFoodPreferenceProfile: vi.fn(),
  updateFoodPreferenceProfile: vi.fn(),
  getMyFoodPreferences: vi.fn(),
  updateMyFoodPreferences: vi.fn(),
  getDishSuggestions: vi.fn(),
}))

const authState = vi.hoisted(() => ({
  accountId: "account-food-a",
  permissions: ["admin.everything"],
}))

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal()),
  ...apiMocks,
}))

vi.mock("../auth/AuthProvider", async (importOriginal) => ({
  ...(await importOriginal()),
  useAuth: () => ({
    status: "authenticated",
    user: {
      account_id: authState.accountId,
      person_id: personId,
      full_name: "测试用户",
      email: "test@example.com",
      force_password_change: false,
      permissions: authState.permissions,
    },
  }),
}))

function catalogFixture(
  accessRevision = revisionA,
  allowedActions: CuisineCatalog["allowed_actions"] = [],
): CuisineCatalog {
  return {
    items: [
      {
        id: cuisineIds.sichuan,
        name: "川菜",
        sort_order: 1,
        is_active: true,
        is_other: false,
      },
      {
        id: cuisineIds.cantonese,
        name: "粤菜",
        sort_order: 2,
        is_active: true,
        is_other: false,
      },
      {
        id: cuisineIds.hunan,
        name: "湘菜",
        sort_order: 3,
        is_active: true,
        is_other: false,
      },
      {
        id: cuisineIds.shandong,
        name: "鲁菜",
        sort_order: 4,
        is_active: true,
        is_other: false,
      },
      {
        id: cuisineIds.other,
        name: "其他",
        sort_order: 5,
        is_active: true,
        is_other: true,
      },
    ],
    allowed_actions: allowedActions,
    access_revision: accessRevision,
  }
}

function otherOnlyCatalog(): CuisineCatalog {
  const catalog = catalogFixture()
  return {
    ...catalog,
    items: catalog.items.filter((item) => item.is_other),
  }
}

function profileFixture(
  overrides: Partial<FoodPreferenceProfile> = {},
): FoodPreferenceProfile {
  return {
    person_id: personId,
    version: 2,
    cuisines: [
      {
        cuisine_option_id: cuisineIds.sichuan,
        name: "川菜",
        custom_name: null,
      },
    ],
    dishes: ["鱼香肉丝"],
    updated_at: "2026-08-12T10:00:00+08:00",
    allowed_actions: ["update"],
    access_revision: revisionA,
    ...overrides,
  }
}

function selfFixture(
  profileOverrides: Partial<FoodPreferenceProfile> = {},
  responseOverrides: Partial<FoodPreferenceSelfResponse> = {},
): FoodPreferenceSelfResponse {
  return {
    profile: profileFixture(profileOverrides),
    onboarding_required: true,
    onboarding_completed_at: null,
    ...responseOverrides,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function testClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
}

type PanelProps = React.ComponentProps<typeof FoodPreferencesPanel>

function renderPanel(
  props: PanelProps,
  options: { client?: QueryClient; route?: string } = {},
) {
  const client = options.client ?? testClient()
  const view = render(
    <MemoryRouter initialEntries={[options.route ?? "/food"]}>
      <QueryClientProvider client={client}>
        <FoodPreferencesPanel {...props} />
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return { ...view, client }
}

function emptyProfile(overrides: Partial<FoodPreferenceProfile> = {}) {
  return profileFixture({ cuisines: [], dishes: [], version: 0, ...overrides })
}

function privateError(status: 403 | 404) {
  return new ProblemDetailsError({
    type: "about:blank",
    title: "Not found",
    status,
    detail: "资源不存在",
    code: "food_preference_not_found",
    request_id: "request-private",
  })
}

beforeEach(() => {
  authState.accountId = accountId
  authState.permissions = ["admin.everything"]
  for (const mock of Object.values(apiMocks)) mock.mockReset()
  apiMocks.getFoodPreferenceCatalog.mockResolvedValue(catalogFixture())
  apiMocks.getFoodPreferenceProfile.mockResolvedValue(profileFixture())
  apiMocks.getMyFoodPreferences.mockResolvedValue(selfFixture())
  apiMocks.updateFoodPreferenceProfile.mockResolvedValue(
    profileFixture({ version: 3 }),
  )
  apiMocks.updateMyFoodPreferences.mockResolvedValue(
    selfFixture({ version: 3 }, { onboarding_required: false }),
  )
  apiMocks.getDishSuggestions.mockResolvedValue({
    items: [],
    access_revision: revisionA,
  })
})

afterEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  vi.clearAllMocks()
})

describe("authorization and revision barrier", () => {
  it("uses only profile allowed_actions for edit authority", async () => {
    apiMocks.getFoodPreferenceProfile.mockResolvedValueOnce(
      profileFixture({ allowed_actions: [] }),
    )
    renderPanel({ mode: "profile", personId })

    expect(await screen.findByText("鱼香肉丝")).toBeVisible()
    expect(screen.queryByRole("button", { name: "编辑餐饮偏好" })).not.toBeInTheDocument()

    authState.permissions = []
    apiMocks.getFoodPreferenceProfile.mockResolvedValueOnce(profileFixture())
    renderPanel({ mode: "profile", personId: otherPersonId })

    expect(
      await screen.findByRole("button", { name: "编辑餐饮偏好" }),
    ).toBeVisible()
  })

  it("hides all stale content during initial load and catalog refetch", async () => {
    const firstCatalog = deferred<CuisineCatalog>()
    const secondCatalog = deferred<CuisineCatalog>()
    apiMocks.getFoodPreferenceCatalog
      .mockReturnValueOnce(firstCatalog.promise)
      .mockReturnValueOnce(secondCatalog.promise)
    const { client } = renderPanel({ mode: "profile", personId })

    expect(
      await screen.findByText("正在确认餐饮偏好访问权限…"),
    ).toBeVisible()
    expect(screen.queryByText("鱼香肉丝")).not.toBeInTheDocument()
    firstCatalog.resolve(catalogFixture())
    expect(await screen.findByText("鱼香肉丝")).toBeVisible()

    act(() => {
      void client.invalidateQueries({ queryKey: catalog(accountId) })
    })
    expect(
      await screen.findByText("正在确认餐饮偏好访问权限…"),
    ).toBeVisible()
    expect(screen.queryByText("鱼香肉丝")).not.toBeInTheDocument()
    secondCatalog.resolve(catalogFixture())
    expect(await screen.findByText("鱼香肉丝")).toBeVisible()

    const catalogQuery = client.getQueryCache().find({
      queryKey: catalog(accountId),
    })
    const catalogOptions = catalogQuery?.options as
      | { staleTime?: number; gcTime?: number }
      | undefined
    expect(catalogOptions?.staleTime).toBe(0)
    expect(catalogOptions?.gcTime).toBe(0)
  })

  it("stays fail closed after a cached catalog refetch fails until explicit reauthorization succeeds", async () => {
    const user = userEvent.setup()
    const failedCatalog = deferred<CuisineCatalog>()
    const retriedCatalog = deferred<CuisineCatalog>()
    apiMocks.getFoodPreferenceCatalog
      .mockResolvedValueOnce(catalogFixture())
      .mockReturnValueOnce(failedCatalog.promise)
      .mockReturnValueOnce(retriedCatalog.promise)
    const { client } = renderPanel({ mode: "profile", personId })

    expect(await screen.findByText("鱼香肉丝")).toBeVisible()
    act(() => {
      void client.invalidateQueries({ queryKey: catalog(accountId) })
    })
    expect(
      await screen.findByText("正在确认餐饮偏好访问权限…"),
    ).toBeVisible()
    expect(screen.queryByText("鱼香肉丝")).not.toBeInTheDocument()

    await act(async () => {
      failedCatalog.reject(new Error("catalog unavailable"))
      await failedCatalog.promise.catch(() => undefined)
    })
    expect(
      await screen.findByText("暂时无法确认餐饮偏好访问权限。"),
    ).toHaveAttribute("role", "alert")
    const retry = screen.getByRole("button", {
      name: "重新确认餐饮偏好访问权限",
    })
    expect(retry.closest(".foodPreferencesPanel")).not.toBeNull()
    expect(screen.queryByText("鱼香肉丝")).not.toBeInTheDocument()

    await user.click(retry)
    expect(screen.queryByText("鱼香肉丝")).not.toBeInTheDocument()
    retriedCatalog.resolve(catalogFixture())
    expect(await screen.findByText("鱼香肉丝")).toBeVisible()
  })

  it("purges old revisions for only the current account before detail loads", async () => {
    const client = testClient()
    const oldCurrentKey = person(accountId, revisionB, personId)
    const otherAccountKey = person(otherAccountId, revisionB, personId)
    client.setQueryData(oldCurrentKey, { private: "current account" })
    client.setQueryData(otherAccountKey, { private: "other account" })

    renderPanel({ mode: "profile", personId }, { client })

    expect(await screen.findByText("鱼香肉丝")).toBeVisible()
    expect(client.getQueryData(oldCurrentKey)).toBeUndefined()
    expect(client.getQueryData(otherAccountKey)).toEqual({
      private: "other account",
    })
    expect(apiMocks.getFoodPreferenceProfile).toHaveBeenCalledTimes(1)
  })

  it.each([403, 404] as const)(
    "clears the current account cache and calls profile revocation on private %s",
    async (status) => {
      const client = testClient()
      const onAccessRevoked = vi.fn()
      const currentKey = person(accountId, revisionA, otherPersonId)
      const otherKey = person(otherAccountId, revisionA, personId)
      client.setQueryData(currentKey, { private: "current" })
      client.setQueryData(otherKey, { private: "other" })
      apiMocks.getFoodPreferenceProfile.mockRejectedValueOnce(privateError(status))

      renderPanel(
        { mode: "profile", personId, onAccessRevoked },
        { client },
      )

      await waitFor(() => expect(onAccessRevoked).toHaveBeenCalledTimes(1))
      expect(client.getQueryData(currentKey)).toBeUndefined()
      expect(client.getQueryData(catalog(accountId))).toBeUndefined()
      expect(client.getQueryData(otherKey)).toEqual({ private: "other" })
    },
  )

  it.each([403, 404] as const)(
    "clears the current account cache and calls self revocation on private %s",
    async (status) => {
      const client = testClient()
      const onAccessRevoked = vi.fn()
      const currentKey = person(accountId, revisionA, otherPersonId)
      const otherKey = foodPreferenceKeys.self(otherAccountId, revisionA)
      client.setQueryData(currentKey, { private: "current" })
      client.setQueryData(otherKey, { private: "other" })
      apiMocks.getMyFoodPreferences.mockRejectedValueOnce(privateError(status))

      renderPanel(
        {
          mode: "self",
          intent: "profile",
          onAccessRevoked,
        },
        { client },
      )

      await waitFor(() => expect(onAccessRevoked).toHaveBeenCalledTimes(1))
      expect(client.getQueryData(currentKey)).toBeUndefined()
      expect(client.getQueryData(catalog(accountId))).toBeUndefined()
      expect(client.getQueryData(otherKey)).toEqual({ private: "other" })
    },
  )

  it("hides cached detail data before a background private failure revokes access", async () => {
    const client = testClient()
    let staleDetailVisibleAtRevocation = false
    const onAccessRevoked = vi.fn(() => {
      staleDetailVisibleAtRevocation =
        screen.queryByText("鱼香肉丝") !== null
    })
    renderPanel(
      { mode: "profile", personId, onAccessRevoked },
      { client },
    )
    expect(await screen.findByText("鱼香肉丝")).toBeVisible()

    apiMocks.getFoodPreferenceProfile.mockRejectedValueOnce(privateError(404))
    await act(() =>
      client.invalidateQueries({
        queryKey: person(accountId, revisionA, personId),
        exact: true,
      }),
    )

    await waitFor(() => expect(onAccessRevoked).toHaveBeenCalledTimes(1))
    expect(staleDetailVisibleAtRevocation).toBe(false)
  })

  it("fails closed on a response revision mismatch and reauthorizes the catalog", async () => {
    const user = userEvent.setup()
    const reauthorizedCatalog = deferred<CuisineCatalog>()
    const retriedCatalog = deferred<CuisineCatalog>()
    apiMocks.getFoodPreferenceCatalog
      .mockResolvedValueOnce(catalogFixture())
      .mockReturnValueOnce(reauthorizedCatalog.promise)
      .mockReturnValueOnce(retriedCatalog.promise)
    apiMocks.getFoodPreferenceProfile
      .mockResolvedValueOnce(
        profileFixture({
          access_revision: revisionB,
          dishes: ["旧响应"],
        }),
      )
      .mockResolvedValueOnce(profileFixture({ dishes: ["新响应"] }))
    const client = testClient()
    const currentKey = person(accountId, revisionA, personId)
    const otherAccountKey = person(otherAccountId, revisionA, personId)
    client.setQueryData(otherAccountKey, { private: "other account" })
    renderPanel({ mode: "profile", personId }, { client })

    await waitFor(() =>
      expect(apiMocks.getFoodPreferenceCatalog).toHaveBeenCalledTimes(2),
    )
    await waitFor(() => expect(client.getQueryData(currentKey)).toBeUndefined())
    expect(client.getQueryData(otherAccountKey)).toEqual({
      private: "other account",
    })
    expect(apiMocks.getFoodPreferenceProfile).toHaveBeenCalledTimes(1)
    expect(screen.queryByText("旧响应")).not.toBeInTheDocument()

    await act(async () => {
      reauthorizedCatalog.reject(new Error("reauthorization unavailable"))
      await reauthorizedCatalog.promise.catch(() => undefined)
    })
    expect(
      await screen.findByText("暂时无法确认餐饮偏好访问权限。"),
    ).toHaveAttribute("role", "alert")
    expect(apiMocks.getFoodPreferenceProfile).toHaveBeenCalledTimes(1)
    await user.click(
      screen.getByRole("button", {
        name: "重新确认餐饮偏好访问权限",
      }),
    )
    expect(apiMocks.getFoodPreferenceProfile).toHaveBeenCalledTimes(1)

    retriedCatalog.resolve(catalogFixture())
    await waitFor(() =>
      expect(apiMocks.getFoodPreferenceProfile).toHaveBeenCalledTimes(2),
    )
    expect(await screen.findByText("新响应")).toBeVisible()
  })

  it("reauthorizes and refetches a mismatched self response before rendering it", async () => {
    const reauthorizedCatalog = deferred<CuisineCatalog>()
    apiMocks.getFoodPreferenceCatalog
      .mockResolvedValueOnce(catalogFixture())
      .mockReturnValueOnce(reauthorizedCatalog.promise)
    apiMocks.getMyFoodPreferences
      .mockResolvedValueOnce(
        selfFixture({ access_revision: revisionB, dishes: ["旧响应"] }),
      )
      .mockResolvedValueOnce(selfFixture({ dishes: ["本人资料"] }))
    const client = testClient()
    const currentKey = foodPreferenceKeys.self(accountId, revisionA)
    const otherAccountKey = foodPreferenceKeys.self(otherAccountId, revisionA)
    client.setQueryData(otherAccountKey, { private: "other account" })
    renderPanel({ mode: "self", intent: "profile" }, { client })

    await waitFor(() =>
      expect(apiMocks.getFoodPreferenceCatalog).toHaveBeenCalledTimes(2),
    )
    await waitFor(() => expect(client.getQueryData(currentKey)).toBeUndefined())
    expect(client.getQueryData(otherAccountKey)).toEqual({
      private: "other account",
    })
    expect(apiMocks.getMyFoodPreferences).toHaveBeenCalledTimes(1)
    expect(screen.queryByText("旧响应")).not.toBeInTheDocument()

    reauthorizedCatalog.resolve(catalogFixture())
    await waitFor(() =>
      expect(apiMocks.getMyFoodPreferences).toHaveBeenCalledTimes(2),
    )
    expect(await screen.findByText("本人资料")).toBeVisible()
    expect(screen.queryByText("旧响应")).not.toBeInTheDocument()
  })

  it("offers an explicit retry after an ordinary detail failure", async () => {
    const user = userEvent.setup()
    apiMocks.getFoodPreferenceProfile
      .mockRejectedValueOnce(new Error("profile unavailable"))
      .mockResolvedValueOnce(profileFixture())
    renderPanel({ mode: "profile", personId })

    expect(
      await screen.findByText("暂时无法读取餐饮偏好。"),
    ).toHaveAttribute("role", "alert")
    expect(screen.queryByText("鱼香肉丝")).not.toBeInTheDocument()
    const retry = screen.getByRole("button", { name: "重新加载餐饮偏好" })
    expect(retry.closest(".foodPreferencesPanel")).not.toBeNull()
    await user.click(retry)
    expect(await screen.findByText("鱼香肉丝")).toBeVisible()
  })
})

describe("mode request boundaries", () => {
  it("keeps profile and self reads and writes on their exact API functions", async () => {
    const user = userEvent.setup()
    const profileView = renderPanel({ mode: "profile", personId })
    await user.click(
      await screen.findByRole("button", { name: "编辑餐饮偏好" }),
    )
    await user.click(screen.getByRole("button", { name: "保存餐饮偏好" }))
    await waitFor(() =>
      expect(apiMocks.updateFoodPreferenceProfile).toHaveBeenCalledTimes(1),
    )
    expect(apiMocks.getMyFoodPreferences).not.toHaveBeenCalled()
    expect(apiMocks.updateMyFoodPreferences).not.toHaveBeenCalled()
    profileView.unmount()
    apiMocks.getFoodPreferenceProfile.mockClear()
    apiMocks.updateFoodPreferenceProfile.mockClear()

    renderPanel({ mode: "self", intent: "profile" })
    await user.click(
      await screen.findByRole("button", { name: "编辑餐饮偏好" }),
    )
    await user.click(screen.getByRole("button", { name: "保存餐饮偏好" }))
    await waitFor(() =>
      expect(apiMocks.updateMyFoodPreferences).toHaveBeenCalledTimes(1),
    )
    expect(apiMocks.getFoodPreferenceProfile).not.toHaveBeenCalled()
    expect(apiMocks.updateFoodPreferenceProfile).not.toHaveBeenCalled()
  })

  it("draft mode performs no profile GET or PUT and renders no save button", async () => {
    const onChange = vi.fn()
    renderPanel({
      mode: "draft",
      personId,
      required: true,
      value: { cuisines: [], dishes: [] },
      onChange,
    })

    expect(await screen.findByText("菜系 0/3")).toBeVisible()
    expect(apiMocks.getFoodPreferenceProfile).not.toHaveBeenCalled()
    expect(apiMocks.getMyFoodPreferences).not.toHaveBeenCalled()
    expect(apiMocks.updateFoodPreferenceProfile).not.toHaveBeenCalled()
    expect(apiMocks.updateMyFoodPreferences).not.toHaveBeenCalled()
    expect(screen.queryByRole("button", { name: /保存|完成填写/ })).not.toBeInTheDocument()
  })
})

describe("selection editor", () => {
  it("directly adds three cuisine names when the catalog only contains Other", async () => {
    const user = userEvent.setup()
    apiMocks.getFoodPreferenceCatalog.mockResolvedValue(otherOnlyCatalog())
    apiMocks.getFoodPreferenceProfile.mockResolvedValue(emptyProfile())
    renderPanel({ mode: "profile", personId })
    await user.click(
      await screen.findByRole("button", { name: "编辑餐饮偏好" }),
    )

    const input = screen.getByLabelText("菜系名称")
    await user.type(input, "川菜")
    await user.click(screen.getByRole("button", { name: "添加菜系" }))
    await user.type(input, "粤菜{Enter}")
    await user.type(input, "东北菜{Enter}")

    expect(screen.getByText("菜系 3/3")).toBeVisible()
    expect(
      screen.getByRole("button", { name: "移除菜系 川菜" }),
    ).toBeVisible()
    expect(screen.queryByText("其他：川菜")).not.toBeInTheDocument()
    expect(input).toBeDisabled()

    await user.click(screen.getByRole("button", { name: "保存餐饮偏好" }))
    await waitFor(() =>
      expect(apiMocks.updateFoodPreferenceProfile).toHaveBeenCalledWith(
        personId,
        {
          expected_version: 0,
          cuisines: [
            { cuisine_option_id: cuisineIds.other, custom_name: "川菜" },
            { cuisine_option_id: cuisineIds.other, custom_name: "粤菜" },
            { cuisine_option_id: cuisineIds.other, custom_name: "东北菜" },
          ],
          dishes: [],
        },
        undefined,
      ),
    )
  })

  it("rejects duplicate directly entered cuisines after normalization", async () => {
    const user = userEvent.setup()
    apiMocks.getFoodPreferenceCatalog.mockResolvedValue(otherOnlyCatalog())
    apiMocks.getFoodPreferenceProfile.mockResolvedValue(emptyProfile())
    renderPanel({ mode: "profile", personId })
    await user.click(
      await screen.findByRole("button", { name: "编辑餐饮偏好" }),
    )

    const input = screen.getByLabelText("菜系名称")
    await user.type(input, "ＳＩＣＨＵＡＮ{Enter}")
    await user.type(input, " sichuan {Enter}")

    expect(screen.getAllByRole("button", { name: /移除菜系/ })).toHaveLength(1)
    expect(screen.getByText("不能添加重复菜系。")).toBeVisible()
  })

  it("rejects a direct cuisine matching a standard option", async () => {
    const user = userEvent.setup()
    apiMocks.getFoodPreferenceProfile.mockResolvedValue(emptyProfile())
    renderPanel({ mode: "profile", personId })
    await user.click(
      await screen.findByRole("button", { name: "编辑餐饮偏好" }),
    )
    await user.click(screen.getByRole("button", { name: "添加菜系 川菜" }))

    await user.type(screen.getByLabelText("菜系名称"), " 川菜 {Enter}")

    expect(screen.getAllByRole("button", { name: /移除菜系/ })).toHaveLength(1)
    expect(screen.getByText("不能添加重复菜系。")).toBeVisible()
  })

  it("uses a renamed standard option's current catalog name for duplicate checks", async () => {
    const user = userEvent.setup()
    const catalog = catalogFixture()
    apiMocks.getFoodPreferenceCatalog.mockResolvedValue({
      ...catalog,
      items: catalog.items.map((item) =>
        item.id === cuisineIds.sichuan ? { ...item, name: "新川菜" } : item,
      ),
    })
    apiMocks.getFoodPreferenceProfile.mockResolvedValue(
      profileFixture({
        cuisines: [
          {
            cuisine_option_id: cuisineIds.sichuan,
            name: "旧川菜",
            custom_name: null,
          },
        ],
      }),
    )
    renderPanel({ mode: "profile", personId })
    await user.click(
      await screen.findByRole("button", { name: "编辑餐饮偏好" }),
    )

    await user.type(screen.getByLabelText("菜系名称"), "新川菜{Enter}")

    expect(screen.getAllByRole("button", { name: /移除菜系/ })).toHaveLength(1)
    expect(screen.getByText("不能添加重复菜系。")).toBeVisible()
  })

  it("renders ordered 0/3 to 3/3 counters and prevents duplicate and fourth items", async () => {
    const user = userEvent.setup()
    apiMocks.getFoodPreferenceProfile.mockResolvedValueOnce(emptyProfile())
    renderPanel({ mode: "profile", personId })
    await user.click(
      await screen.findByRole("button", { name: "编辑餐饮偏好" }),
    )

    const counters = screen.getAllByRole("status")
    expect(counters.map((counter) => counter.textContent)).toEqual([
      "菜系 0/3",
      "菜品 0/3",
    ])
    await user.click(screen.getByRole("button", { name: "添加菜系 川菜" }))
    await user.click(screen.getByRole("button", { name: "添加菜系 粤菜" }))
    await user.click(screen.getByRole("button", { name: "添加菜系 湘菜" }))
    expect(screen.getByText("菜系 3/3")).toHaveAttribute("aria-live", "polite")
    expect(screen.getByRole("button", { name: "添加菜系 鲁菜" })).toBeDisabled()

    const input = screen.getByLabelText("菜品名称")
    await user.type(input, "Rice Bowl")
    await user.click(screen.getByRole("button", { name: "添加菜品" }))
    await user.type(input, "ＲＩＣＥ　ＢＯＷＬ")
    await user.click(screen.getByRole("button", { name: "添加菜品" }))
    expect(screen.getAllByText("Rice Bowl")).toHaveLength(1)
    expect(screen.getByText("不能添加重复菜品。")).toBeVisible()
    await user.clear(input)
    await user.type(input, "宫保鸡丁")
    await user.click(screen.getByRole("button", { name: "添加菜品" }))
    await user.type(input, "麻婆豆腐")
    await user.click(screen.getByRole("button", { name: "添加菜品" }))
    expect(screen.getByText("菜品 3/3")).toHaveAttribute("aria-live", "polite")
    expect(screen.getByLabelText("菜品名称")).toBeDisabled()
    expect(screen.getByRole("button", { name: "添加菜品" })).toBeDisabled()
  })

  it("keeps missing catalog selections removable and supports keyboard chip removal", async () => {
    const user = userEvent.setup()
    apiMocks.getFoodPreferenceProfile.mockResolvedValueOnce(
      profileFixture({
        cuisines: [
          {
            cuisine_option_id: cuisineIds.retired,
            name: "旧菜系",
            custom_name: null,
          },
        ],
        dishes: ["旧菜品"],
      }),
    )
    renderPanel({ mode: "profile", personId })
    await user.click(
      await screen.findByRole("button", { name: "编辑餐饮偏好" }),
    )

    const cuisineRemove = screen.getByRole("button", {
      name: "移除菜系 旧菜系",
    })
    cuisineRemove.focus()
    await user.keyboard("{Backspace}")
    expect(screen.queryByText("旧菜系")).not.toBeInTheDocument()
    const dishRemove = screen.getByRole("button", { name: "移除菜品 旧菜品" })
    dishRemove.focus()
    await user.keyboard("{Delete}")
    expect(screen.queryByText("旧菜品")).not.toBeInTheDocument()
  })

  it("adds a nonblank direct cuisine and renders focusable suggestions only for nonblank input", async () => {
    const user = userEvent.setup()
    apiMocks.getFoodPreferenceProfile.mockResolvedValueOnce(emptyProfile())
    apiMocks.getDishSuggestions.mockResolvedValue({
      items: [{ name: "宫保鸡丁" }],
      access_revision: revisionA,
    })
    renderPanel({ mode: "profile", personId })
    await user.click(
      await screen.findByRole("button", { name: "编辑餐饮偏好" }),
    )
    expect(apiMocks.getDishSuggestions).not.toHaveBeenCalled()

    const addCuisine = screen.getByRole("button", { name: "添加菜系" })
    expect(addCuisine).toBeDisabled()
    await user.type(screen.getByLabelText("菜系名称"), "客家菜")
    expect(addCuisine).toBeEnabled()
    await user.click(addCuisine)
    expect(
      screen.getByRole("button", { name: "移除菜系 客家菜" }),
    ).toBeVisible()

    await user.type(screen.getByLabelText("菜品名称"), "宫")
    const suggestion = await screen.findByRole("button", {
      name: "使用建议 宫保鸡丁",
    })
    expect(suggestion).toBeEnabled()
    await user.click(suggestion)
    expect(screen.getByRole("button", { name: "移除菜品 宫保鸡丁" })).toBeVisible()
    expect(apiMocks.getDishSuggestions).toHaveBeenCalledWith(
      "宫",
      10,
      expect.any(AbortSignal),
    )
  })

  it("fails closed and reauthorizes before showing a same-revision suggestion", async () => {
    const user = userEvent.setup()
    const reauthorizedCatalog = deferred<CuisineCatalog>()
    apiMocks.getFoodPreferenceProfile.mockResolvedValueOnce(emptyProfile())
    apiMocks.getFoodPreferenceCatalog
      .mockResolvedValueOnce(catalogFixture())
      .mockReturnValueOnce(reauthorizedCatalog.promise)
    apiMocks.getDishSuggestions
      .mockResolvedValueOnce({
        items: [{ name: "过期建议" }],
        access_revision: revisionB,
      })
      .mockResolvedValueOnce({
        items: [{ name: "新鲜建议" }],
        access_revision: revisionA,
      })
    const client = testClient()
    const currentKey = foodPreferenceKeys.suggestions(
      accountId,
      revisionA,
      "建",
      10,
    )
    const otherAccountKey = foodPreferenceKeys.suggestions(
      otherAccountId,
      revisionA,
      "建",
      10,
    )
    client.setQueryData(otherAccountKey, { private: "other account" })
    renderPanel({ mode: "profile", personId }, { client })
    await user.click(
      await screen.findByRole("button", { name: "编辑餐饮偏好" }),
    )
    await user.type(screen.getByLabelText("菜品名称"), "建")

    await waitFor(() =>
      expect(apiMocks.getFoodPreferenceCatalog).toHaveBeenCalledTimes(2),
    )
    await waitFor(() => expect(client.getQueryData(currentKey)).toBeUndefined())
    expect(client.getQueryData(otherAccountKey)).toEqual({
      private: "other account",
    })
    expect(screen.queryByText("过期建议")).not.toBeInTheDocument()
    expect(apiMocks.getDishSuggestions).toHaveBeenCalledTimes(1)

    reauthorizedCatalog.resolve(catalogFixture())
    expect(
      await screen.findByRole("button", { name: "使用建议 新鲜建议" }),
    ).toBeVisible()
    expect(apiMocks.getDishSuggestions).toHaveBeenCalledTimes(2)
  })

  it("keeps manual dish entry available and explicitly retries an ordinary suggestion failure", async () => {
    const user = userEvent.setup()
    apiMocks.getFoodPreferenceProfile.mockResolvedValueOnce(emptyProfile())
    apiMocks.getDishSuggestions.mockRejectedValue(
      new Error("suggestions unavailable"),
    )
    renderPanel({ mode: "profile", personId })
    await user.click(
      await screen.findByRole("button", { name: "编辑餐饮偏好" }),
    )

    const input = screen.getByLabelText("菜品名称")
    await user.type(input, "手")
    expect(
      await screen.findByRole("button", { name: "重新加载菜品建议" }),
    ).toBeVisible()
    await user.click(screen.getByRole("button", { name: "添加菜品" }))
    expect(screen.getByRole("button", { name: "移除菜品 手" })).toBeVisible()

    await user.type(input, "建")
    expect(
      await screen.findByRole("button", { name: "重新加载菜品建议" }),
    ).toBeVisible()
    apiMocks.getDishSuggestions.mockResolvedValueOnce({
      items: [{ name: "建议菜" }],
      access_revision: revisionA,
    })
    await user.click(
      screen.getByRole("button", { name: "重新加载菜品建议" }),
    )
    expect(
      await screen.findByRole("button", { name: "使用建议 建议菜" }),
    ).toBeVisible()
  })

  it.each([
    ["Straße", "STRASSE"],
    ["σ", "ς"],
  ])(
    "uses Python-compatible Unicode casefolding to reject duplicate dishes %s and %s",
    async (first, duplicate) => {
      const user = userEvent.setup()
      apiMocks.getFoodPreferenceProfile.mockResolvedValueOnce(emptyProfile())
      renderPanel({ mode: "profile", personId })
      await user.click(
        await screen.findByRole("button", { name: "编辑餐饮偏好" }),
      )

      const input = screen.getByLabelText("菜品名称")
      await user.type(input, first)
      await user.click(screen.getByRole("button", { name: "添加菜品" }))
      await user.type(input, duplicate)
      await user.click(screen.getByRole("button", { name: "添加菜品" }))

      expect(
        screen.getByRole("button", { name: `移除菜品 ${first}` }),
      ).toBeVisible()
      expect(
        screen.queryByRole("button", { name: `移除菜品 ${duplicate}` }),
      ).not.toBeInTheDocument()
      expect(screen.getByText("不能添加重复菜品。")).toBeVisible()
    },
  )

  it("checks the 40-character limit before casefold expansion", async () => {
    const user = userEvent.setup()
    apiMocks.getFoodPreferenceProfile.mockResolvedValueOnce(emptyProfile())
    renderPanel({ mode: "profile", personId })
    await user.click(
      await screen.findByRole("button", { name: "编辑餐饮偏好" }),
    )

    await user.type(screen.getByLabelText("菜品名称"), "ß".repeat(40))
    await user.click(screen.getByRole("button", { name: "添加菜品" }))

    expect(screen.getByRole("button", { name: "保存餐饮偏好" })).toBeEnabled()
  })

  it("treats a private suggestion failure as account-wide profile revocation", async () => {
    const user = userEvent.setup()
    const client = testClient()
    const onAccessRevoked = vi.fn()
    const currentKey = person(accountId, revisionA, otherPersonId)
    const otherKey = person(otherAccountId, revisionA, otherPersonId)
    client.setQueryData(currentKey, { private: "current" })
    client.setQueryData(otherKey, { private: "other" })
    apiMocks.getDishSuggestions.mockRejectedValueOnce(privateError(403))
    renderPanel(
      { mode: "profile", personId, onAccessRevoked },
      { client },
    )
    await user.click(
      await screen.findByRole("button", { name: "编辑餐饮偏好" }),
    )
    await user.type(screen.getByLabelText("菜品名称"), "撤权")

    await waitFor(() => expect(onAccessRevoked).toHaveBeenCalledTimes(1))
    expect(client.getQueryData(currentKey)).toBeUndefined()
    expect(client.getQueryData(otherKey)).toEqual({ private: "other" })
  })

  it("hides cached suggestions before a background private failure revokes access", async () => {
    const user = userEvent.setup()
    const client = testClient()
    let staleSuggestionVisibleAtRevocation = false
    const onAccessRevoked = vi.fn(() => {
      staleSuggestionVisibleAtRevocation =
        screen.queryByRole("button", { name: "使用建议 宫保鸡丁" }) !== null
    })
    apiMocks.getFoodPreferenceProfile.mockResolvedValueOnce(emptyProfile())
    apiMocks.getDishSuggestions.mockResolvedValueOnce({
      items: [{ name: "宫保鸡丁" }],
      access_revision: revisionA,
    })
    renderPanel(
      { mode: "profile", personId, onAccessRevoked },
      { client },
    )
    await user.click(
      await screen.findByRole("button", { name: "编辑餐饮偏好" }),
    )
    await user.type(screen.getByLabelText("菜品名称"), "宫")
    expect(
      await screen.findByRole("button", { name: "使用建议 宫保鸡丁" }),
    ).toBeVisible()

    apiMocks.getDishSuggestions.mockRejectedValueOnce(privateError(403))
    await act(() =>
      client.invalidateQueries({
        queryKey: foodPreferenceKeys.suggestions(
          accountId,
          revisionA,
          "宫",
          10,
        ),
        exact: true,
      }),
    )

    await waitFor(() => expect(onAccessRevoked).toHaveBeenCalledTimes(1))
    expect(staleSuggestionVisibleAtRevocation).toBe(false)
  })

  it("reports controlled required draft validity and becomes valid at 1+1", async () => {
    const user = userEvent.setup()

    function DraftHarness() {
      const [value, setValue] = useState<FoodPreferenceDraft>({
        cuisines: [],
        dishes: [],
      })
      return (
        <FoodPreferencesPanel
          mode="draft"
          personId={personId}
          required={true}
          value={value}
          onChange={setValue}
        />
      )
    }

    const client = testClient()
    render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <DraftHarness />
        </QueryClientProvider>
      </MemoryRouter>,
    )
    expect(
      await screen.findByText("请至少选择 1 个菜系和 1 个菜品。"),
    ).toBeVisible()
    await user.click(screen.getByRole("button", { name: "添加菜系 川菜" }))
    expect(screen.getByText("请至少选择 1 个菜系和 1 个菜品。")).toBeVisible()
    await user.type(screen.getByLabelText("菜品名称"), "鱼香肉丝")
    await user.click(screen.getByRole("button", { name: "添加菜品" }))
    expect(screen.getByText("餐饮偏好已填写完整。")).toBeVisible()
  })

  it("allows a daily profile to save an empty 0+0 preference", async () => {
    const user = userEvent.setup()
    renderPanel({ mode: "profile", personId })
    await user.click(
      await screen.findByRole("button", { name: "编辑餐饮偏好" }),
    )
    await user.click(screen.getByRole("button", { name: "移除菜系 川菜" }))
    await user.click(screen.getByRole("button", { name: "移除菜品 鱼香肉丝" }))
    const save = screen.getByRole("button", { name: "保存餐饮偏好" })
    expect(save).toBeEnabled()
    await user.click(save)

    await waitFor(() =>
      expect(apiMocks.updateFoodPreferenceProfile).toHaveBeenCalledWith(
        personId,
        { expected_version: 2, cuisines: [], dishes: [] },
        undefined,
      ),
    )
  })
})

describe("onboarding, writes, and conflict recovery", () => {
  it("skips with zero writes and requires 1+1 plus explicit confirmation", async () => {
    const user = userEvent.setup()
    const onSkipped = vi.fn()
    const onCommitted = vi.fn()
    const localWrite = vi.spyOn(Storage.prototype, "setItem")
    apiMocks.getMyFoodPreferences.mockResolvedValueOnce({
      ...selfFixture({}, { onboarding_required: true }),
      profile: emptyProfile(),
    })
    renderPanel({
      mode: "self",
      intent: "onboarding",
      onSkipped,
      onCommitted,
    })

    expect(await screen.findByText("菜系 0/3")).toBeVisible()
    await user.click(screen.getByRole("button", { name: "稍后填写" }))
    expect(onSkipped).toHaveBeenCalledTimes(1)
    expect(apiMocks.updateMyFoodPreferences).not.toHaveBeenCalled()
    expect(localWrite).not.toHaveBeenCalled()

    const submit = screen.getByRole("button", { name: "完成填写" })
    await user.type(screen.getByLabelText("菜品名称"), "鱼香肉丝")
    await user.click(screen.getByRole("button", { name: "添加菜品" }))
    expect(submit).toBeDisabled()
    await user.click(screen.getByRole("button", { name: "移除菜品 鱼香肉丝" }))
    await user.click(screen.getByRole("button", { name: "添加菜系 川菜" }))
    expect(submit).toBeDisabled()
    await user.type(screen.getByLabelText("菜品名称"), "鱼香肉丝")
    await user.click(screen.getByRole("button", { name: "添加菜品" }))
    expect(submit).toBeDisabled()
    await user.click(
      screen.getByRole("checkbox", {
        name: "我确认以上是本人的餐饮偏好",
      }),
    )
    expect(submit).toBeEnabled()
    await user.click(submit)
    await waitFor(() =>
      expect(apiMocks.updateMyFoodPreferences).toHaveBeenCalledWith(
        {
          expected_version: 0,
          cuisines: [
            { cuisine_option_id: cuisineIds.sichuan, custom_name: null },
          ],
          dishes: ["鱼香肉丝"],
        },
        undefined,
      ),
    )
    await waitFor(() => expect(onCommitted).toHaveBeenCalledTimes(1))
    expect(localWrite).not.toHaveBeenCalled()
  })

  it("requires explicit confirmation even when onboarding is prefilled", async () => {
    const user = userEvent.setup()
    renderPanel({ mode: "self", intent: "onboarding" })

    const submit = await screen.findByRole("button", { name: "完成填写" })
    expect(screen.getByText("鱼香肉丝")).toBeVisible()
    expect(submit).toBeDisabled()
    await user.click(
      screen.getByRole("checkbox", {
        name: "我确认以上是本人的餐饮偏好",
      }),
    )
    expect(submit).toBeEnabled()
  })

  it("keeps mutation variables text-free, uses gcTime 0, and invalidates only safe roots", async () => {
    const user = userEvent.setup()
    const write = deferred<FoodPreferenceProfile>()
    const catalogInvalidation = deferred<void>()
    const onCommitted = vi.fn()
    const storageWrite = vi.spyOn(Storage.prototype, "setItem")
    apiMocks.updateFoodPreferenceProfile.mockReturnValueOnce(write.promise)
    const client = testClient()
    const invalidate = vi
      .spyOn(client, "invalidateQueries")
      .mockReturnValueOnce(catalogInvalidation.promise)
      .mockResolvedValue(undefined)
    renderPanel({ mode: "profile", personId, onCommitted }, { client })
    await user.click(
      await screen.findByRole("button", { name: "编辑餐饮偏好" }),
    )
    await user.click(screen.getByRole("button", { name: "保存餐饮偏好" }))

    const mutation = client.getMutationCache().getAll()[0]
    expect(mutation?.state.variables).toBe(2)
    expect(JSON.stringify(mutation?.state.variables)).not.toContain("鱼香肉丝")
    expect(mutation?.options.gcTime).toBe(0)
    write.resolve(profileFixture({ version: 3 }))

    await waitFor(() => expect(client.getMutationCache().getAll()).toHaveLength(0))
    expect(onCommitted).not.toHaveBeenCalled()
    catalogInvalidation.resolve()
    await waitFor(() => expect(onCommitted).toHaveBeenCalledTimes(1))
    expect(invalidate.mock.calls.map(([filters]) => filters?.queryKey)).toEqual([
      catalog(accountId),
      person(accountId, revisionA, personId),
      [...revisionRoot(accountId, revisionA), "statistics"],
    ])
    expect(storageWrite).not.toHaveBeenCalled()
  })

  it("preserves the exact local draft and retries a 409 only after explicit confirmation with the new version", async () => {
    const user = userEvent.setup()
    const latestProfile = deferred<FoodPreferenceProfile>()
    const conflict = new ProblemDetailsError({
      type: "about:blank",
      title: "Conflict",
      status: 409,
      detail: "版本冲突",
      code: "food_preference_version_changed",
      request_id: "request-conflict",
    })
    apiMocks.getFoodPreferenceProfile
      .mockResolvedValueOnce(profileFixture())
      .mockReturnValueOnce(latestProfile.promise)
    apiMocks.updateFoodPreferenceProfile
      .mockRejectedValueOnce(conflict)
      .mockResolvedValueOnce(profileFixture({ version: 8, dishes: ["本地草稿"] }))
    const { client } = renderPanel({ mode: "profile", personId })
    await user.click(
      await screen.findByRole("button", { name: "编辑餐饮偏好" }),
    )
    await user.click(screen.getByRole("button", { name: "移除菜品 鱼香肉丝" }))
    await user.type(screen.getByLabelText("菜品名称"), "本地草稿")
    await user.click(screen.getByRole("button", { name: "添加菜品" }))
    await user.click(screen.getByRole("button", { name: "保存餐饮偏好" }))

    expect(
      await screen.findByText("资料已被更新，请确认后重试"),
    ).toBeVisible()
    expect(screen.getByText("本地草稿")).toBeVisible()
    expect(screen.queryByText("服务端的新菜品")).not.toBeInTheDocument()
    expect(apiMocks.updateFoodPreferenceProfile).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(client.getMutationCache().getAll()).toHaveLength(0))
    latestProfile.resolve(
      profileFixture({ version: 7, dishes: ["服务端的新菜品"] }),
    )

    await user.click(
      await screen.findByRole("button", { name: "确认并重试" }),
    )
    await waitFor(() =>
      expect(apiMocks.updateFoodPreferenceProfile).toHaveBeenCalledTimes(2),
    )
    expect(apiMocks.updateFoodPreferenceProfile.mock.calls.map(([input]) => input)).toEqual([
      personId,
      personId,
    ])
    expect(apiMocks.updateFoodPreferenceProfile.mock.calls.map(([, input]) => input)).toEqual([
      expect.objectContaining({ expected_version: 2, dishes: ["本地草稿"] }),
      expect.objectContaining({ expected_version: 7, dishes: ["本地草稿"] }),
    ])
  })

  it("allows cancel after a 409 refresh and restores the latest accepted server profile", async () => {
    const user = userEvent.setup()
    const conflict = new ProblemDetailsError({
      type: "about:blank",
      title: "Conflict",
      status: 409,
      detail: "版本冲突",
      code: "food_preference_version_changed",
      request_id: "request-conflict-cancel",
    })
    apiMocks.getFoodPreferenceProfile
      .mockResolvedValueOnce(profileFixture())
      .mockResolvedValueOnce(
        profileFixture({ version: 7, dishes: ["服务端的新菜品"] }),
      )
    apiMocks.updateFoodPreferenceProfile.mockRejectedValueOnce(conflict)
    const { client } = renderPanel({ mode: "profile", personId })
    await user.click(
      await screen.findByRole("button", { name: "编辑餐饮偏好" }),
    )
    await user.click(screen.getByRole("button", { name: "移除菜品 鱼香肉丝" }))
    await user.type(screen.getByLabelText("菜品名称"), "本地草稿")
    await user.click(screen.getByRole("button", { name: "添加菜品" }))
    await user.click(screen.getByRole("button", { name: "保存餐饮偏好" }))

    expect(
      await screen.findByRole("button", { name: "确认并重试" }),
    ).toBeVisible()
    const cancel = screen.getByRole("button", { name: "取消编辑" })
    expect(cancel).toBeEnabled()
    await waitFor(() => expect(client.getMutationCache().getAll()).toHaveLength(0))
    await user.click(cancel)

    expect(screen.getByText("服务端的新菜品")).toBeVisible()
    expect(screen.queryByText("本地草稿")).not.toBeInTheDocument()
    expect(screen.queryByText("资料已被更新，请确认后重试")).not.toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "确认并重试" }),
    ).not.toBeInTheDocument()
    expect(client.getMutationCache().getAll()).toHaveLength(0)
  })

  it("ignores a pending mutation settlement after the panel unmounts", async () => {
    const user = userEvent.setup()
    const write = deferred<FoodPreferenceProfile>()
    const onCommitted = vi.fn()
    apiMocks.updateFoodPreferenceProfile.mockReturnValueOnce(write.promise)
    const client = testClient()
    const invalidate = vi.spyOn(client, "invalidateQueries")
    const view = renderPanel(
      { mode: "profile", personId, onCommitted },
      { client },
    )

    await user.click(
      await screen.findByRole("button", { name: "编辑餐饮偏好" }),
    )
    await user.click(screen.getByRole("button", { name: "保存餐饮偏好" }))
    await waitFor(() =>
      expect(apiMocks.updateFoodPreferenceProfile).toHaveBeenCalledTimes(1),
    )

    view.unmount()
    await act(async () => {
      write.resolve(profileFixture({ version: 3 }))
      await write.promise
      await Promise.resolve()
    })

    expect(invalidate).not.toHaveBeenCalled()
    expect(onCommitted).not.toHaveBeenCalled()
  })

  it("hides profile data before notifying about a private mutation failure", async () => {
    const user = userEvent.setup()
    let staleDetailVisibleAtRevocation = false
    const onAccessRevoked = vi.fn(() => {
      staleDetailVisibleAtRevocation =
        screen.queryByText("鱼香肉丝") !== null
    })
    apiMocks.updateFoodPreferenceProfile.mockRejectedValueOnce(
      privateError(403),
    )
    renderPanel({ mode: "profile", personId, onAccessRevoked })

    await user.click(
      await screen.findByRole("button", { name: "编辑餐饮偏好" }),
    )
    await user.click(screen.getByRole("button", { name: "保存餐饮偏好" }))

    await waitFor(() => expect(onAccessRevoked).toHaveBeenCalledTimes(1))
    expect(staleDetailVisibleAtRevocation).toBe(false)
  })

  it("ignores stale mutation settlements after route and authorization revision changes", async () => {
    const user = userEvent.setup()
    const routeWrite = deferred<FoodPreferenceProfile>()
    const revisionWrite = deferred<FoodPreferenceProfile>()
    const onCommitted = vi.fn()
    apiMocks.updateFoodPreferenceProfile
      .mockReturnValueOnce(routeWrite.promise)
      .mockReturnValueOnce(revisionWrite.promise)

    function RouteHarness() {
      const navigate = useNavigate()
      return (
        <>
          <button type="button" onClick={() => navigate("/next")}>换路由</button>
          <FoodPreferencesPanel
            mode="profile"
            personId={personId}
            onCommitted={onCommitted}
          />
        </>
      )
    }

    const client = testClient()
    render(
      <MemoryRouter initialEntries={["/first"]}>
        <QueryClientProvider client={client}>
          <RouteHarness />
        </QueryClientProvider>
      </MemoryRouter>,
    )
    await user.click(
      await screen.findByRole("button", { name: "编辑餐饮偏好" }),
    )
    await user.click(screen.getByRole("button", { name: "保存餐饮偏好" }))
    await user.click(screen.getByRole("button", { name: "换路由" }))
    routeWrite.resolve(profileFixture({ version: 3 }))
    await act(async () => Promise.resolve())
    expect(onCommitted).not.toHaveBeenCalled()

    await user.click(
      await screen.findByRole("button", { name: "编辑餐饮偏好" }),
    )
    await user.click(screen.getByRole("button", { name: "保存餐饮偏好" }))
    apiMocks.getFoodPreferenceCatalog.mockResolvedValueOnce(
      catalogFixture(revisionB),
    )
    apiMocks.getFoodPreferenceProfile.mockResolvedValueOnce(
      profileFixture({ access_revision: revisionB }),
    )
    await act(() => client.invalidateQueries({ queryKey: catalog(accountId) }))
    await waitFor(() =>
      expect(
        client.getQueryData(person(accountId, revisionB, personId)),
      ).toMatchObject({ access_revision: revisionB }),
    )
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "编辑餐饮偏好" }),
      ).toBeVisible(),
    )
    revisionWrite.resolve(profileFixture({ version: 4 }))
    await act(async () => Promise.resolve())
    expect(onCommitted).not.toHaveBeenCalled()
  })
})

it("keeps the CSS responsive, wrapping, text-safe, and touch-sized without overflow hiding", () => {
  const css = readFileSync(
    "src/foodPreferences/food-preferences.css",
    "utf8",
  )
  const tokens = readFileSync("src/styles/tokens.css", "utf8")
  const declaredTokens = new Set(
    [...tokens.matchAll(/(--[a-z0-9-]+)\s*:/giu)].map((match) => match[1]),
  )
  const usedTokens = new Set(
    [...css.matchAll(/var\((--[a-z0-9-]+)\)/giu)].map((match) => match[1]),
  )
  expect(
    [...usedTokens].filter((token) => !declaredTokens.has(token)),
  ).toEqual([])
  expect(css).toMatch(/width:\s*100%/)
  expect(css).toMatch(/min-width:\s*0/)
  expect(css).toMatch(/overflow-wrap:\s*anywhere/)
  expect(css).toMatch(/flex-wrap:\s*wrap/)
  expect(css).toMatch(/grid-template-columns:\s*minmax\(0,\s*1fr\)/)
  expect(css).toMatch(/repeat\([^;]*minmax\(min\(100%,\s*\d+px\),\s*1fr\)/)
  expect(css).toMatch(/min-height:\s*44px/)
  expect(css).toMatch(/min-width:\s*44px/)
  expect(css).not.toMatch(/overflow-x:\s*hidden/)
})
