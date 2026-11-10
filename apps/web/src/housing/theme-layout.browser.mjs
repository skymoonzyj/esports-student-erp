// Real Chromium layout checks against production React components and CSS.
// Run from apps/web: node src/housing/theme-layout.browser.mjs
/* global process, document, innerWidth, getComputedStyle, scrollTo, console */
import assert from "node:assert/strict"
import { readFile, mkdir } from "node:fs/promises"
import path from "node:path"
import { chromium } from "@playwright/test"
import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { createServer } from "vite"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter, Routes, Route } from "react-router-dom"

const root = process.cwd()
const output = process.env.HOUSING_THEME_OUTPUT ?? path.join(root, "../../.superpowers/sdd/2026-10-27-erp-dual-theme-redesign/task-4-visual")
const server = await createServer({ root, server: { middlewareMode: true }, appType: "custom" })
const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE_PATH } : {}) })
const failures = []
const check = (value, message) => { if (!value) failures.push(message) }
try {
  const { RoomCard } = await server.ssrLoadModule("/src/housing/RoomCard.tsx")
  const { HousingBulkActions } = await server.ssrLoadModule("/src/housing/HousingBulkActions.tsx")
  const { HousingDialogShell } = await server.ssrLoadModule("/src/housing/HousingDialogShell.tsx")
  const { InvitedHousingSummary } = await server.ssrLoadModule("/src/invitations/InvitedHousingSummary.tsx")
  const { PeoplePage } = await server.ssrLoadModule("/src/people/PeoplePage.tsx")
  const { PersonDetailPage } = await server.ssrLoadModule("/src/people/PersonDetailPage.tsx")
  const { PersonForm } = await server.ssrLoadModule("/src/people/PersonForm.tsx")
  const css = (await Promise.all(["styles/tokens.css", "styles/global.css", "housing/housing.css", "people/people.css", "people/personAffiliations.css", "invitations/invitations.css"].map(file => readFile(path.join(root, "src", file), "utf8")))).join("\n")
  const noop = () => {}
  const room = {
    id: "visual-room", building_id: "building", floor_id: "floor", building_name: "演示楼栋", floor_name: "三层", room_number: "A-301", room_type: "double", capacity: 2,
    area_square_meters: "24", verification_status: "verified", service_status: "available", cleaning_status: "ready", daily_cleaning_enabled: true,
    photo_count: 2, current_occupants: 0, remaining_capacity: 2, current_gender: null, has_future_reservation: true, occupancy_status: "unoccupied", is_assignable: true,
    status_text: "空闲，可入住", pending_transfer_count: 0, occupants: [], notes: "演示房间备注，可用于观察中文换行与长内容展示。",
    allowed_actions: { edit: true, verify: true, delete: true, complete_cleaning: true },
  }
  const card = (overrides, selected = false) => renderToStaticMarkup(React.createElement(RoomCard, { room: { ...room, ...overrides }, selected, onSelectedChange: noop, onViewDetails: noop, onEdit: noop, onVerify: noop, onDelete: noop, onCompleteCleaning: noop }))
  const cards = [card({}), card({ room_number: "A-302", cleaning_status: "pending", is_assignable: false }, true), card({ room_number: "A-303", current_occupants: 1, occupancy_status: "occupied", remaining_capacity: 1, occupants: [{ id: "stay-demo", person_name: "演示学员", person_type: "student", sex: "male" }] }), card({ room_number: "A-304", verification_status: "pending_verification", service_status: "maintenance", is_assignable: false, status_text: "维修中，不可入住" })]
  const bulk = renderToStaticMarkup(React.createElement(HousingBulkActions, { action: "verify", count: 2, pending: false, problems: [], onActionChange: noop, onClear: noop, onConfirm: noop }))
  const client = new QueryClient({ defaultOptions: { queries: { staleTime: Infinity, retry: false } } })
  client.setQueryData(["people-counts", "legacy"], { total: 24, student: 12, guardian: 5, staff: 4, coach: 1, life_teacher: 1, customer_service: 1, administration: 1, other_staff: 0, visitor: 3, team_member: 8, team_manager: 2, in_team: 8, pending_team_allocation: 3, assigned_team_allocation: 5 })
  const filters = { type: "", staffType: "", role: "", teamAllocationStatus: "", page: 1, pageSize: 20 }
  client.setQueryData(["people", filters, "allocation-visible", "legacy"], { items: [{ id: "visual-person", full_name: "演示学员", nickname: "昵称示例", person_types: ["student"], staff_type: null, identity_number: "**************1234", phone: "138****0000", email: "e***@example.invalid", date_of_birth: "2010-04-15", age_status: "minor", guardian_status: "pending", account_status: "not_activated", assisted_kind: "student", roles: [] }], total: 24, page: 1, page_size: 20 })
  const people = renderToStaticMarkup(React.createElement(QueryClientProvider, { client }, React.createElement(MemoryRouter, null, React.createElement(PeoplePage, { canCreate: true }))))
  const permissions = new Set(["people.identity.reveal", "people.phone.reveal"])
  const detailScope = JSON.stringify(["legacy", null, [], [], [...permissions], []])
  const person = { ...client.getQueryData(["people", filters, "allocation-visible", "legacy"]).items[0], created_at: "2026-10-01T00:00:00Z", sex: "male", assisted_kind: null, student_number: "DEMO-001", student_status: "active", staff_type: null, visit_reason: null, linked_student_id: null }
  client.setQueryData(["person", "visual-person", "legacy", detailScope], person)
  const profile = renderToStaticMarkup(React.createElement(QueryClientProvider, { client }, React.createElement(MemoryRouter, { initialEntries: ["/people/visual-person"] }, React.createElement(Routes, null, React.createElement(Route, { path: "/people/:personId", element: React.createElement(PersonDetailPage, { permissions }) })))))
  const personForm = renderToStaticMarkup(React.createElement(QueryClientProvider, { client }, React.createElement(MemoryRouter, null, React.createElement(PersonForm))))
  const summary = renderToStaticMarkup(React.createElement(InvitedHousingSummary, { summary: { status: "checked_in", stay: { building_name: "演示楼栋", floor_name: "三层", room_name: "A-301", duration_mode: "long_term", planned_check_in_at: "2026-10-01", actual_check_in_at: "2026-10-01" } } }))
  const housing = `<section class="housingPage"><header class="housingPage__header"><div><p class="pageEyebrow">HOUSING</p><h1>住宿房态</h1><p>按楼栋、楼层与入住状态查看房间</p></div><div class="housingPage__headerActions"><button>导出房态</button><button>楼栋管理</button></div></header><div class="housingPage__layout"><aside class="housingFilters"><header><h2>筛选房间</h2></header><div class="housingFilters__body"><label>楼栋<select><option>全部楼栋</option></select></label><fieldset><legend>服务状态</legend><label><input type="checkbox" />正常</label><label><input type="checkbox" />维修</label></fieldset></div></aside><div class="housingPage__content">${bulk}<section class="housingSummary">${["房间总数", "已用房间数", "空房", "可入住", "退房待保洁", "待核对", "剩余床位", "入住率"].map((label, index) => `<button class="housingSummary__item"><span><span class="housingSummary__label">${label}</span> <strong class="housingSummary__value">${index + 2}</strong></span></button>`).join("")}</section><div class="housingPage__meta"><strong>筛选结果 4 间</strong><span>演示数据</span></div><ul class="housingRoomGrid">${cards.map(value => `<li>${value}</li>`).join("")}</ul></div></div></section>`
  const invitations = `<section class="invitationPage"><header><h1>我的邀请</h1><p>受邀人员与住宿信息</p></header><div class="invitationFilters"><label>住宿状态<select><option>全部状态</option></select></label></div><table class="invitedPeople"><thead><tr><th>人员</th><th>住宿</th></tr></thead><tbody><tr><td data-label="人员">演示学员</td><td data-label="住宿">${summary}</td></tr></tbody></table><nav><button>上一页</button><span>第 1 页</span><button>下一页</button></nav></section>`
  const dialog = renderToStaticMarkup(React.createElement(HousingDialogShell, { title: "完成房间 A-302 保洁", pending: false, error: null, onClose: noop }, React.createElement("form", null, React.createElement("fieldset", null, React.createElement("label", null, "保洁说明", React.createElement("textarea", { defaultValue: "已确认床位、清洁状态和照片。" })), React.createElement("footer", null, React.createElement("button", null, "取消"), React.createElement("button", null, "确认完成保洁"))))))
  await mkdir(output, { recursive: true })
  const page = await browser.newPage()
  const densities = {}
  for (const theme of ["clear", "command"]) {
    for (const width of [320, 390, 768, 1024, 1181, 1200, 1440]) {
      await page.setViewportSize({ width, height: 960 })
      // The actual overview unmounts the desktop sidebar below 720px. Match that
      // layout here, without replacing the production cards or directory.
      await page.setContent(`<html data-ui-theme="${theme}"><head><meta charset="utf-8"><style>${css}\n.layoutFixture{padding:16px;min-width:0}@media(min-width:1024px){.layoutFixture{margin-left:240px}}@media(max-width:719px){.housingPage__layout > .housingFilters{display:none}}</style></head><body><main class="layoutFixture">${housing}${people}${invitations}</main></body></html>`)
      const measurements = await page.evaluate(() => {
        const rect = element => { const { x, width, height } = element.getBoundingClientRect(); return { x, width, height } }
        const card = document.querySelector(".housingRoomCard")
        const count = document.querySelector(".peopleCountCard")
        return { overflow: document.documentElement.scrollWidth > innerWidth, clippedPeople: [...document.querySelectorAll(".peopleTableHeader,.peopleTableRow")].some(element => element.scrollWidth > element.clientWidth), card: rect(card), count: rect(count), countColumns: getComputedStyle(document.querySelector(".peopleCounts")).gridTemplateColumns.split(" ").length, roomButtons: [...document.querySelectorAll(".housingRoomCard__actions button")].map(rect), peopleLink: rect(document.querySelector(".peopleDetailLink")), invitationButtons: [...document.querySelectorAll(".invitationPage button")].map(rect), summaryGap: getComputedStyle(document.querySelector(".housingSummary")).gap, selectedShadow: getComputedStyle(document.querySelectorAll(".housingRoomCard")[1]).boxShadow, unselectedShadow: getComputedStyle(card).boxShadow }
      })
      const label = `${theme} ${width}px`
      check(!measurements.overflow, `${label}: no document-level horizontal overflow`)
      check(!measurements.clippedPeople, `${label}: directory columns are not silently clipped by a desktop sidebar`)
      if (width < 768) {
        check(measurements.countColumns === 3, `${label}: statistics use three mobile columns`)
        check(measurements.count.height <= 78, `${label}: mobile statistics keep the directory within reach`)
      }
      check(measurements.card.width >= 240, `${label}: room card remains readable (>=240px)`)
      check(measurements.roomButtons.every(button => button.height >= 44), `${label}: room actions keep 44px touch targets`)
      check(measurements.peopleLink.height >= 44, `${label}: personnel detail link keeps 44px touch target`)
      check(measurements.invitationButtons.every(button => button.height >= 44), `${label}: invitation actions keep 44px touch targets`)
      check(measurements.selectedShadow !== measurements.unselectedShadow, `${label}: selected room is visually distinct beyond checkbox`)
      if (width === 1440) densities[theme] = measurements
      await page.locator(".housingRoomCard__actions button").first().focus()
      await page.keyboard.press("Tab")
      check(await page.locator(".housingRoomCard__actions button").nth(1).evaluate(element => element === document.activeElement), `${label}: keyboard focus order remains usable`)
      await page.evaluate(() => scrollTo(0, 0))
      await page.screenshot({ path: path.join(output, `${theme}-${width}.png`), fullPage: true })
      await page.locator(".peoplePage").screenshot({ path: path.join(output, `${theme}-${width}-people.png`) })
      await page.setContent(`<html data-ui-theme="${theme}"><head><style>${css}\n.layoutFixture{padding:16px;min-width:0}@media(min-width:1024px){.layoutFixture{margin-left:240px}}</style></head><body><main class="layoutFixture">${profile}${personForm}</main></body></html>`)
      check(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${label}: real personnel profile and form do not overflow`)
      check(await page.locator(".personAffiliations .detailCardHeader button").evaluate(element => element.getBoundingClientRect().height >= 44), `${label}: affiliation disclosure keeps a 44px target`)
      check(await page.locator(".personDetailPage .detailList button").evaluateAll(elements => elements.every(element => element.getBoundingClientRect().height >= 44)), `${label}: sensitive data reveal targets remain accessible`)
      const name = page.locator('.personFormPage input[name="full_name"]')
      await name.fill("未保存演示草稿")
      await page.evaluate(next => document.documentElement.dataset.uiTheme = next, theme === "clear" ? "command" : "clear")
      check(await name.inputValue() === "未保存演示草稿", `${label}: CSS theme change preserves native form input`)
      await page.evaluate(next => document.documentElement.dataset.uiTheme = next, theme)
      if ([320, 390, 1440].includes(width)) await page.locator(".personDetailPage").screenshot({ path: path.join(output, `${theme}-${width}-profile.png`) })
      await page.setContent(`<html data-ui-theme="${theme}"><head><style>${css}</style></head><body>${dialog}</body></html>`)
      check(await page.locator(".housingDialog").evaluate(element => element.scrollWidth <= element.clientWidth && element.getBoundingClientRect().right <= innerWidth), `${label}: housing dialog contains long title and form`)
    }
  }
  check(densities.command.card.height < densities.clear.card.height, "command desktop: room grid is denser than clear")
  check(densities.command.count.height < densities.clear.count.height, "command desktop: statistics strip is denser than clear")
  check(densities.command.summaryGap === "0px", "command desktop: connected statistics strip")
  assert.deepEqual(failures, [], failures.join("\n"))
  console.log("PASS: both themes at 320/390/768/1024/1181/1200/1440px; actual RoomCard, PeoplePage, PersonDetailPage, PersonForm, invitations summary, dialog, focus, touch targets and density. Screenshots:", output)
} finally {
  await browser.close()
  await server.close()
}
