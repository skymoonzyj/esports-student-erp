import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"
import { useState } from "react"

import * as api from "./api"
import { AssetCategorySelect } from "./AssetCategorySelect"

const catalog: api.AssetCategoryCatalog = {
  groups: [
    { key: "computers", label: "电脑与电竞设备", options: ["台式电脑", "显示器"] },
    { key: "furniture", label: "家具与宿舍设施", options: ["电竞椅"] },
  ],
  legacy_categories: ["旧训练设备", "旧办公设备"],
}

afterEach(() => vi.restoreAllMocks())

function renderSelect(props: Partial<React.ComponentProps<typeof AssetCategorySelect>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onChange = vi.fn()
  const view = render(<QueryClientProvider client={client}><label htmlFor="category">分类</label><AssetCategorySelect id="category" value="" onChange={onChange} mode="create" accountId="account-a" authorized {...props} /></QueryClientProvider>)
  return { ...view, client, onChange }
}

function renderControlledEdit(originalCategory: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  function Edit() {
    const [category, setCategory] = useState(originalCategory)
    return <><label htmlFor="category">分类</label><AssetCategorySelect id="category" value={category} originalValue={originalCategory} onChange={setCategory} mode="edit" accountId="account-a" authorized /></>
  }
  return render(<QueryClientProvider client={client}><Edit /></QueryClientProvider>)
}

it("restores the exact original legacy value after choosing a preset", async () => {
  vi.spyOn(api, "listAssetCategories").mockResolvedValue(catalog)
  renderControlledEdit("  原有专用设备  ")
  const select = screen.getByRole("combobox", { name: "分类" })
  await waitFor(() => expect(within(select).getByRole("option", { name: "显示器" })).toBeVisible())
  await userEvent.selectOptions(select, "显示器")
  expect(select).toHaveValue("显示器")
  await userEvent.selectOptions(select, "  原有专用设备  ")
  expect(select).toHaveValue("  原有专用设备  ")
})

it("restores the original legacy value after clearing", async () => {
  vi.spyOn(api, "listAssetCategories").mockResolvedValue(catalog)
  renderControlledEdit("原有专用设备")
  const select = screen.getByRole("combobox", { name: "分类" })
  await userEvent.selectOptions(select, "")
  expect(select).toHaveValue("")
  await userEvent.selectOptions(select, "原有专用设备")
  expect(select).toHaveValue("原有专用设备")
})

it("does not duplicate a preset original or the currently selected legacy category", async () => {
  vi.spyOn(api, "listAssetCategories").mockResolvedValue(catalog)
  const { unmount } = renderControlledEdit("显示器")
  const presetSelect = screen.getByRole("combobox", { name: "分类" })
  await waitFor(() => expect(within(presetSelect).getAllByRole("option", { name: "显示器" })).toHaveLength(1))
  unmount()
  renderControlledEdit("原有专用设备")
  const legacySelect = screen.getByRole("combobox", { name: "分类" })
  expect(within(legacySelect).getAllByRole("option", { name: "原有专用设备" })).toHaveLength(1)
})

it("offers server groups as selectable leaves and omits history when creating", async () => {
  vi.spyOn(api, "listAssetCategories").mockResolvedValue(catalog)
  const { onChange } = renderSelect()
  const select = await screen.findByRole("combobox", { name: "分类" })
  await waitFor(() => expect(within(select).getByRole("option", { name: "显示器" })).toBeVisible())
  expect(within(select).getByRole("group", { name: "电脑与电竞设备" })).toBeInTheDocument()
  expect(within(select).queryByRole("option", { name: "旧训练设备" })).not.toBeInTheDocument()
  await userEvent.selectOptions(select, "显示器")
  expect(onChange).toHaveBeenCalledWith("显示器")
})

it("keeps an unknown edit value and allows clearing it after a failed catalog request", async () => {
  const list = vi.spyOn(api, "listAssetCategories").mockRejectedValueOnce(new Error("network")).mockResolvedValue(catalog)
  const { onChange } = renderSelect({ mode: "edit", value: "原有专用设备" })
  const select = screen.getByRole("combobox", { name: "分类" })
  expect(select).toHaveValue("原有专用设备")
  expect(await screen.findByRole("alert")).toHaveTextContent("分类暂时无法读取")
  await userEvent.selectOptions(select, "")
  expect(onChange).toHaveBeenCalledWith("")
  await userEvent.click(screen.getByRole("button", { name: "重试读取分类" }))
  await waitFor(() => expect(list).toHaveBeenCalledTimes(2))
  expect(await within(select).findByRole("option", { name: "显示器" })).toBeVisible()
})

it("shows historic filter categories from other pages but never reuses another account's catalog", async () => {
  const list = vi.spyOn(api, "listAssetCategories")
    .mockResolvedValueOnce(catalog)
    .mockResolvedValueOnce({ groups: [{ key: "other", label: "其他资产", options: ["其他"] }], legacy_categories: ["乙账户旧值"] })
  const { rerender, client, onChange } = renderSelect({ mode: "filter" })
  const select = screen.getByRole("combobox", { name: "分类" })
  await waitFor(() => expect(within(select).getByRole("option", { name: "旧办公设备" })).toBeVisible())
  await userEvent.selectOptions(select, "旧办公设备")
  expect(onChange).toHaveBeenCalledWith("旧办公设备")
  rerender(<QueryClientProvider client={client}><label htmlFor="category">分类</label><AssetCategorySelect id="category" value="" onChange={onChange} mode="filter" accountId="account-b" authorized /></QueryClientProvider>)
  expect(within(select).queryByRole("option", { name: "旧办公设备" })).not.toBeInTheDocument()
  await waitFor(() => expect(within(select).getByRole("option", { name: "乙账户旧值" })).toBeVisible())
  expect(list).toHaveBeenCalledTimes(2)
})

it("does not request the directory on an inactive or unauthorized surface", () => {
  const list = vi.spyOn(api, "listAssetCategories").mockResolvedValue(catalog)
  renderSelect({ active: false })
  renderSelect({ authorized: false })
  expect(list).not.toHaveBeenCalled()
})
