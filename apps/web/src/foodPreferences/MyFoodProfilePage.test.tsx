import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, useLocation } from "react-router-dom"
import { beforeEach, expect, it, vi } from "vitest"

import { portalKeys } from "../portal/api"
import { MyFoodProfilePage } from "./MyFoodProfilePage"

const accountId = "10000000-0000-4000-8000-000000000098"
const panelState = vi.hoisted(() => ({
  props: [] as Array<Record<string, unknown>>,
  identifierProps: [] as Array<Record<string, unknown>>,
  selfProfileProps: [] as Array<Record<string, unknown>>,
}))

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({
    status: "authenticated",
    user: {
      account_id: accountId,
      person_id: "20000000-0000-4000-8000-000000000098",
      full_name: "本人资料用户",
      email: "my-food-profile@example.test",
      force_password_change: false,
      permissions: [],
      scoped_permissions: [],
    },
  }),
}))

vi.mock("./FoodPreferencesPanel", () => ({
  FoodPreferencesPanel: (props: {
    onAccessRevoked?: () => void
  } & Record<string, unknown>) => {
    panelState.props.push(props as Record<string, unknown>)
    return (
      <div data-testid="food-preferences-panel">
        本人餐饮偏好面板
        <button type="button" onClick={props.onAccessRevoked}>
          模拟撤权
        </button>
      </div>
    )
  },
}))

vi.mock("../auth/AccountIdentifiersPanel", () => ({
  AccountIdentifiersPanel: (props: Record<string, unknown>) => {
    panelState.identifierProps.push(props)
    return <div data-testid="account-identifiers-panel">登录方式面板</div>
  },
}))

vi.mock("../people/SelfProfilePanel", () => ({
  SelfProfilePanel: (props: Record<string, unknown>) => {
    panelState.selfProfileProps.push(props)
    return <div data-testid="self-profile-panel">本人基本信息面板</div>
  },
}))

function LocationProbe() {
  return <p data-testid="location">{useLocation().pathname}</p>
}

function renderPage(client = new QueryClient()) {
  render(
    <MemoryRouter initialEntries={["/me/profile"]}>
      <QueryClientProvider client={client}>
        <MyFoodProfilePage />
        <LocationProbe />
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return client
}

beforeEach(() => {
  panelState.props.length = 0
  panelState.identifierProps.length = 0
  panelState.selfProfileProps.length = 0
})

it("renders the optional self profile without people-management controls", () => {
  renderPage()

  expect(screen.getByRole("heading", { name: "个人资料" })).toBeVisible()
  expect(screen.getByTestId("account-identifiers-panel")).toBeVisible()
  expect(screen.getByTestId("self-profile-panel")).toBeVisible()
  expect(screen.getByTestId("food-preferences-panel")).toBeVisible()
  expect(
    screen.queryByRole("link", { name: "管理成年学员监护邀请" }),
  ).not.toBeInTheDocument()
  expect(panelState.identifierProps.at(-1)).toEqual({ accountId })
  expect(panelState.selfProfileProps.at(-1)).toEqual({
    personId: "20000000-0000-4000-8000-000000000098",
  })
  expect(panelState.props.at(-1)).toEqual(
    expect.objectContaining({ mode: "self", intent: "profile" }),
  )
  expect(panelState.props.at(-1)).not.toHaveProperty("personId")
  expect(screen.queryByText("人员中心")).not.toBeInTheDocument()
  expect(screen.queryByText("编辑人员")).not.toBeInTheDocument()
  expect(screen.queryByText("开通账号")).not.toBeInTheDocument()
})

it("refreshes portal context and returns home after self access is revoked", async () => {
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
