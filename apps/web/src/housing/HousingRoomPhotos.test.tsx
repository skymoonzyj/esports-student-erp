import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, expect, it, vi } from "vitest"
import { ProblemDetailsError } from "../api/client"
import type { HousingRoomDetail } from "./api"
import { fetchRoomPhoto } from "./api"
import { HousingRoomPhotos } from "./HousingRoomPhotos"

vi.mock("./api", async (original) => ({ ...await original(), fetchRoomPhoto: vi.fn() }))
const detail = { access_revision: "r", room: { id: "room", version: 4, allowed_actions: { manage_photos: true } }, photos: [{ id: "photo", slot: 1, updated_at: "now", mime_type: "image/jpeg", byte_size: 10, url: "/private", thumbnail_url: "/private-thumb" }] } as HousingRoomDetail
beforeEach(() => {
  vi.mocked(fetchRoomPhoto).mockReset().mockResolvedValue(new Blob(["pixels"], { type: "image/jpeg" }))
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn().mockReturnValue("blob:private"), revokeObjectURL: vi.fn() }))
})

it("has only three slots, reads originals on click, restores focus and revokes private URLs", async () => {
  const view = render(<HousingRoomPhotos detail={detail} scope="a:r" pending={false} onUpload={vi.fn()} onDelete={vi.fn()} onPrivateFailure={vi.fn()} />)
  expect(screen.getAllByLabelText(/选择第 [123] 张房间照片/)).toHaveLength(3)
  await screen.findByAltText("房间照片 1 缩略图")
  expect(fetchRoomPhoto).not.toHaveBeenCalledWith("room", "photo", "original")
  const opener = screen.getByRole("button", { name: "放大房间照片 1" })
  await userEvent.click(opener)
  await screen.findByAltText("房间照片原图")
  expect(fetchRoomPhoto).toHaveBeenCalledWith("room", "photo", "original")
  await userEvent.keyboard("{Escape}")
  await waitFor(() => expect(opener).toHaveFocus())
  view.unmount()
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:private")
})

it.each([
  { label: "remove and re-add in the same slot", removeFirst: true, id: "new-photo", updated_at: "later" },
  { label: "replace photo identity", removeFirst: false, id: "new-photo", updated_at: "now" },
  { label: "replace content at the same identity", removeFirst: false, id: "photo", updated_at: "later" },
  { label: "remove and re-add the same metadata", removeFirst: true, id: "photo", updated_at: "now" },
])("requires a new click after $label", async ({ removeFirst, id, updated_at }) => {
  const props = { scope: "a:r", pending: false, onUpload: vi.fn(), onDelete: vi.fn(), onPrivateFailure: vi.fn() }
  const view = render(<HousingRoomPhotos {...props} detail={detail} />)
  await screen.findByAltText("房间照片 1 缩略图")
  await userEvent.click(screen.getByRole("button", { name: "放大房间照片 1" }))
  await screen.findByAltText("房间照片原图")
  vi.mocked(fetchRoomPhoto).mockClear()
  if (removeFirst) view.rerender(<HousingRoomPhotos {...props} detail={{ ...detail, photos: [] }} />)
  view.rerender(<HousingRoomPhotos {...props} detail={{ ...detail, photos: [{ ...detail.photos[0], id, updated_at }] }} />)
  await screen.findByAltText("房间照片 1 缩略图")
  expect(screen.queryByRole("dialog", { name: "房间照片 1" })).not.toBeInTheDocument()
  expect(vi.mocked(fetchRoomPhoto).mock.calls.filter((call) => call[2] === "original")).toEqual([])
  await userEvent.click(screen.getByRole("button", { name: "放大房间照片 1" }))
  await screen.findByAltText("房间照片原图")
  expect(fetchRoomPhoto).toHaveBeenCalledWith("room", id, "original")
})

it("keeps the selected replacement and existing image after 409 and never retries blindly", async () => {
  const upload = vi.fn().mockRejectedValue(new ProblemDetailsError({ status: 409, code: "housing_stale_version", detail: "stale", title: "stale", type: "about:blank", request_id: "test" }))
  const props = { scope: "a:r", pending: false, onUpload: upload, onDelete: vi.fn(), onPrivateFailure: vi.fn() }
  const view = render(<HousingRoomPhotos detail={detail} {...props} />)
  await screen.findByAltText("房间照片 1 缩略图")
  const file = new File(["image"], "new.jpg", { type: "image/jpeg" })
  await userEvent.upload(screen.getByLabelText("选择第 1 张房间照片"), file)
  await userEvent.click(screen.getByRole("button", { name: "上传第 1 张照片" }))
  await screen.findByText(/重新核对/)
  view.rerender(<HousingRoomPhotos {...props} detail={{ ...detail, room: { ...detail.room, version: 5 }, photos: [{ ...detail.photos[0], id: "replacement", updated_at: "later" }] }} />)
  expect(screen.getByText("new.jpg")).toBeInTheDocument()
  expect(screen.getByAltText("房间照片 1 缩略图")).toBeInTheDocument()
  expect(upload).toHaveBeenCalledTimes(1)
  expect(screen.getByRole("button", { name: "上传第 1 张照片" })).toBeDisabled()
})

it("is read-only without care permission and discards late thumbnails on unmount", async () => {
  let resolve!: (value: Blob) => void
  vi.mocked(fetchRoomPhoto).mockReturnValue(new Promise((done) => { resolve = done }))
  const view = render(<HousingRoomPhotos detail={{ ...detail, room: { ...detail.room, allowed_actions: { ...detail.room.allowed_actions, manage_photos: false } } }} scope="a:r" pending={false} onUpload={vi.fn()} onDelete={vi.fn()} onPrivateFailure={vi.fn()} />)
  expect(screen.queryByLabelText("选择第 1 张房间照片")).not.toBeInTheDocument()
  view.unmount()
  await act(async () => resolve(new Blob(["late"])))
  expect(URL.createObjectURL).not.toHaveBeenCalled()
})

it("rejects unsupported formats and oversized files before upload", async () => {
  const upload = vi.fn()
  render(<HousingRoomPhotos detail={detail} scope="a:r" pending={false} onUpload={upload} onDelete={vi.fn()} onPrivateFailure={vi.fn()} />)
  fireEvent.change(screen.getByLabelText("选择第 2 张房间照片"), { target: { files: [new File(["bad"], "photo.heic", { type: "image/heic" })] } })
  expect(screen.getByRole("alert")).toHaveTextContent("JPEG、PNG、WebP")
  expect(upload).not.toHaveBeenCalled()
  const oversized = new File(["x"], "big.png", { type: "image/png" })
  Object.defineProperty(oversized, "size", { value: 10 * 1024 * 1024 + 1 })
  fireEvent.change(screen.getByLabelText("选择第 2 张房间照片"), { target: { files: [oversized] } })
  expect(screen.getByRole("alert")).toHaveTextContent("10 MiB")
  expect(upload).not.toHaveBeenCalled()
})

it("retains the previous thumbnail if replacement loading fails", async () => {
  const props = { scope: "a:r", pending: false, onUpload: vi.fn(), onDelete: vi.fn(), onPrivateFailure: vi.fn() }
  const view = render(<HousingRoomPhotos {...props} detail={detail} />)
  await screen.findByAltText("房间照片 1 缩略图")
  vi.mocked(fetchRoomPhoto).mockRejectedValueOnce(new Error("offline"))
  view.rerender(<HousingRoomPhotos {...props} detail={{ ...detail, photos: [{ ...detail.photos[0], id: "replacement" }] }} />)
  await screen.findByText("照片加载失败，暂时保留已显示的图片。")
  expect(screen.getByAltText("房间照片 1 缩略图")).toHaveAttribute("src", "blob:private")
})
