import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  MemoryRouter,
  useLocation,
} from "react-router-dom"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { portalKeys } from "../portal/api"
import {
  foodOnboardingPath,
  FoodOnboardingPage,
} from "./FoodOnboardingPage"

const accountId = "10000000-0000-4000-8000-000000000099"
const panelState = vi.hoisted(() => ({
  props: [] as Array<Record<string, unknown>>,
}))

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({
    status: "authenticated",
    user: {
      account_id: accountId,
      person_id: "20000000-0000-4000-8000-000000000099",
      full_name: "餐饮引导用户",
      email: "food-onboarding@example.test",
      force_password_change: false,
      permissions: [],
      scoped_permissions: [],
    },
  }),
}))

vi.mock("./FoodPreferencesPanel", () => ({
  FoodPreferencesPanel: (props: {
    mode: string
    intent: string
    onSkipped?: () => void
    onCommitted?: (value: unknown) => void
    onAccessRevoked?: () => void
  }) => {
    panelState.props.push(props as unknown as Record<string, unknown>)
    return (
      <div data-testid="food-preferences-panel">
        <button type="button" onClick={props.onSkipped}>
          模拟稍后填写
        </button>
        <button type="button" onClick={() => props.onCommitted?.({})}>
          模拟完成填写
        </button>
        <button type="button" onClick={props.onAccessRevoked}>
          模拟撤权
        </button>
      </div>
    )
  },
}))

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

function LocationProbe() {
  return <p data-testid="location">{useLocation().pathname}</p>
}

function renderPage(client = new QueryClient()) {
  render(
    <MemoryRouter initialEntries={[foodOnboardingPath]}>
      <QueryClientProvider client={client}>
        <FoodOnboardingPage />
        <LocationProbe />
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return client
}

beforeEach(() => {
  panelState.props.length = 0
})

afterEach(() => {
  vi.restoreAllMocks()
})

it("freezes the onboarding path and renders only the self onboarding panel", () => {
  expect(foodOnboardingPath).toBe("/onboarding/food-preferences")

  renderPage()

  expect(
    screen.getByRole("heading", { name: "完善餐饮偏好" }),
  ).toBeVisible()
  expect(panelState.props.at(-1)).toEqual(
    expect.objectContaining({ mode: "self", intent: "onboarding" }),
  )
  expect(panelState.props.at(-1)).not.toHaveProperty("personId")
  expect(screen.queryByText("人员中心")).not.toBeInTheDocument()
})

it("skips directly home without refreshing portal context", async () => {
  const user = userEvent.setup()
  const client = renderPage()
  const refresh = vi.spyOn(client, "refetchQueries")

  await user.click(screen.getByRole("button", { name: "模拟稍后填写" }))

  expect(screen.getByTestId("location")).toHaveTextContent("/")
  expect(refresh).not.toHaveBeenCalled()
})

it("waits for an exact active portal refresh before leaving onboarding", async () => {
  const user = userEvent.setup()
  const client = new QueryClient()
  const pending = deferred<void>()
  const refresh = vi
    .spyOn(client, "refetchQueries")
    .mockReturnValueOnce(pending.promise)
  renderPage(client)

  await user.click(screen.getByRole("button", { name: "模拟完成填写" }))

  expect(refresh).toHaveBeenCalledWith(
    {
      queryKey: portalKeys.context(accountId),
      exact: true,
      type: "active",
    },
    { throwOnError: true },
  )
  expect(screen.getByTestId("location")).toHaveTextContent(foodOnboardingPath)

  await act(async () => pending.resolve())

  await waitFor(() =>
    expect(screen.getByTestId("location")).toHaveTextContent("/"),
  )
})

it("stays on onboarding when the portal refresh fails", async () => {
  const user = userEvent.setup()
  const client = new QueryClient()
  vi.spyOn(client, "refetchQueries").mockRejectedValueOnce(
    new Error("portal unavailable"),
  )
  renderPage(client)

  await user.click(screen.getByRole("button", { name: "模拟完成填写" }))

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "餐饮偏好已保存，但暂时无法刷新待办，请重试。",
  )
  expect(screen.getByTestId("location")).toHaveTextContent(foodOnboardingPath)
})

it("returns to a safe route and refreshes portal context after self revocation", async () => {
  const user = userEvent.setup()
  const client = renderPage()
  const refresh = vi.spyOn(client, "refetchQueries")

  await user.click(screen.getByRole("button", { name: "模拟撤权" }))

  expect(refresh).toHaveBeenCalledWith({
    queryKey: portalKeys.context(accountId),
    exact: true,
    type: "active",
  })
  expect(screen.getByTestId("location")).toHaveTextContent("/")
})
