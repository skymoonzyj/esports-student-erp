import type { Page } from "@playwright/test"
import type { HousingCatalog, HousingOverview, HousingRoom } from "../../src/housing/api"
import type { OperationsOverview } from "../../src/operations/api"

// Synthetic UI fixtures only. Every API request is intercepted; no ERP data is read/written.
const now = "2026-10-27T12:00:00Z"
const id = "10000000-0000-4000-8000-000000000001"
const catalog: HousingCatalog = {
  access_revision: "ui-test-1", can_create_building: true,
  buildings: [{ id: "a", code: "A", name: "A 幢", display_order: 1, is_active: true, notes: null, allowed_actions: [], created_at: now, updated_at: now }],
  floors: [{ id: "a3", building_id: "a", building_name: "A 幢", number: 3, name: "三层", display_order: 3, is_active: true, notes: null, allowed_actions: [], created_at: now, updated_at: now }],
}
const room: HousingRoom = {
  id, building_id: "a", floor_id: "a3", building_code: "A", building_name: "A 幢", building_is_active: true,
  floor_number: 3, floor_name: "三层", floor_is_active: true, room_number: "301", room_type: "double", capacity: 2,
  area_square_meters: "26.00", verification_status: "verified", service_status: "available", notes: "合成测试房间", version: 1,
  current_occupants: 0, remaining_capacity: 2, current_gender: null, has_future_reservation: false,
  occupancy_status: "unoccupied", is_assignable: true, status_text: "空闲，可入住",
  allowed_actions: { create_stay: true, report_issue: true, view_detail: true, manage_care: true, complete_cleaning: false, manage_photos: true, edit: true, delete: true, verify: false },
  occupants: [], future_reservation_count: 0, open_issue_summary: null, pending_transfer_count: 0,
  cleaning_frequency: "daily", daily_cleaning_enabled: true, cleaning_status: "ready", cleaning_pending_at: null, cleaning_completed_at: null, photo_count: 0,
  created_at: now, updated_at: now,
}
const overview: HousingOverview = {
  access_revision: "ui-test-1", as_of: now,
  summary: { daily_cleaning_rooms: 4, weekly_cleaning_rooms: 0, on_demand_cleaning_rooms: 0, pending_cleaning_rooms: 1, total_rooms: 4, occupied_rooms: 1, unoccupied_rooms: 3, assignable_rooms: 2, unassignable_rooms: 2, full_rooms: 0, problem_rooms: 0, pending_verification_rooms: 1, total_capacity: 8, current_occupants: 1, remaining_capacity: 7, occupancy_rate: "12.50", single_rooms: 0, double_rooms: 4, triple_rooms: 0, custom_rooms: 0, functional_rooms: 0 },
  rooms: [room, { ...room, id: "room2", room_number: "302", cleaning_status: "pending", is_assignable: false, status_text: "待保洁" }, { ...room, id: "room3", room_number: "303", verification_status: "pending_verification", is_assignable: false, status_text: "待核对", allowed_actions: { ...room.allowed_actions, verify: true } }, { ...room, id: "room4", room_number: "304" }],
  page: 1, page_size: 20, total: 4,
}
const operations: OperationsOverview = {
  generated_at: now, window_minutes: 60, health: { score: 98, status: "healthy" },
  traffic: { request_count: 1280, client_error_count: 2, client_error_details: [{ status_code: 404, count: 2, reason: "请求资源不存在" }], server_error_count: 0, error_rate_percent: 0.16, requests_per_second: 0.35, average_latency_ms: 32, p50_latency_ms: 22, p95_latency_ms: 82, p99_latency_ms: 110, max_latency_ms: 140,
    series: Array.from({ length: 12 }, (_, index) => ({ starts_at: `2026-10-27T${String(index).padStart(2, "0")}:00:00Z`, request_count: 20 + index * 3, error_count: 0, average_latency_ms: 25 })),
    top_routes: [{ method: "GET", route: "/api/v1/people", request_count: 125, error_count: 0, average_latency_ms: 24 }] },
  database: { status: "healthy", response_time_ms: 4, active_connections: 3, idle_connections: 4, total_connections: 7, max_connections: 100, utilization_percent: 7 },
  runtime: { status: "healthy", cpu_percent: 18, memory_used_mb: 520, memory_limit_mb: 4096, memory_percent: 12.7, process_memory_mb: 210, uptime_seconds: 90000 },
  background_tasks: { status: "healthy", scheduler_status: "healthy", scheduler_last_seen_at: now, scheduler_lag_seconds: 10, scheduler_consecutive_failures: 0, scheduler_last_result_count: 1, total_tasks: 12, queued_tasks: 2, processing_tasks: 1, pending_review_tasks: 3, failed_tasks: 0, needs_attention_tasks: 0 },
}

export async function installUiThemeFixtures(page: Page, theme: "clear" | "command", authenticated = true) {
  let appearance = { theme, revision: 1 }
  const unexpected: string[] = []
  const mutations: string[] = []
  const permissions = ["operations.monitor.read", "operations.appearance.manage", "people.list", "people.read", "people.update", "housing.read", "housing.manage", "housing.catalog.manage", "accounts.read", "teaching.read", "assets.read", "assets.manage", "assets.assign", "assets.export", "procurement.read", "procurement.manage", "procurement.export", "revenue_share.finance", "revenue_share.settle", "food_preferences.statistics.read"]
  await page.route("**/api/v1/**", async route => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace("/api/v1", "")
    const json = (value: unknown, status = 200) => route.fulfill({ status, json: value })
    if (request.method() !== "GET") {
      mutations.push(path)
      if (path === "/operations/appearance" && request.method() === "PUT") {
        const input = request.postDataJSON() as { theme: "clear" | "command"; expected_revision: number }
        if (input.expected_revision !== appearance.revision) return json({ status: 409, detail: "配置已变更", code: "appearance_conflict" }, 409)
        appearance = { theme: input.theme, revision: appearance.revision + 1 }
        return json(appearance)
      }
      unexpected.push(`${request.method()} ${path}`)
      return json({ status: 400, detail: "UI fixture prohibits business mutation" }, 400)
    }
    if (path === "/appearance" || path === "/operations/appearance") return json(appearance)
    if (path === "/auth/me") return authenticated ? json({ account_id: id, person_id: id, full_name: "界面验收管理员", email: "ui@example.test", force_password_change: false, is_super_admin: true, permissions }) : json({ status: 401, detail: "未登录" }, 401)
    if (path === "/auth/csrf") return json({ csrf_token: "synthetic-ui-token" })
    if (path === "/portal/context") return json({ coach_available: false, learning_available: false, payments_available: false, housing_management_available: true, housing_available: false, invitations_available: false, adult_guardian_invitations_available: false, food_profile_available: false, food_preferences_onboarding_required: false, learning_mode: null })
    if (path === "/portal/departure-reminders") return json({ available: true, observed_at: now, business_date: "2026-10-27", timezone: "Asia/Shanghai", next_refresh_at: "2099-01-01T00:00:00Z", window: url.searchParams.get("window") ?? "three_days", today_count: 0, three_day_count: 0, page: 1, page_size: 20, total: 0, items: [] })
    if (path === "/portal/rental-reminders") return json({ available: true, can_manage: true, observed_at: now, business_date: "2026-10-27", next_refresh_at: "2099-01-01T00:00:00Z", page: 1, page_size: 20, total: 0, items: [] })
    if (path === "/team-management/context") return json({ can_open_management: true, can_read_self: false, can_manage_catalog: true, can_assign_managers: true, can_transfer: true })
    if (path === "/personnel-intake/context") return json({ can_submit: true, can_review: true, can_direct_create: true, can_read_managed: true, can_direct_create_pending: true, allowed_team_ids: [], eligible_coaches: [], eligible_teams: [] })
    if (path === "/referrals/me/sources") return json({ sources: [] })
    if (path === "/housing/catalog") return json(catalog)
    if (path === "/housing/property/capabilities") return json({ can_submit: false, can_review: false, can_export: false })
    if (path === "/portal/room-card-reminders") return json({ available: false, observed_at: now, business_date: "2026-10-27", next_refresh_at: "2099-01-01T00:00:00Z", page: 1, page_size: 20, total: 0, items: [] })
    if (path === "/housing/overview") return json(overview)
    if (path === "/people/counts") return json({ total: 2, student: 1, team_member: 1, staff: 0, guardian: 0, visitor: 0, in_team: 1, pending_team_allocation: 1, assigned_team_allocation: 1 })
    if (path === "/people") return json({ items: ["星河测试", "山海测试"].map((name, i) => ({ id: `${id.slice(0, -1)}${i + 1}`, full_name: name, nickname: i ? "山海" : "星河", person_types: ["student"], roles: [], team: { visibility: "allowed", current: null }, identity_number: "测试脱敏证件", phone: "138****0000", email: "student@example.test", sex: "male", date_of_birth: "2004-01-01", staff_type: null, created_at: now, age: 22, age_status: "adult", guardian_status: "not_applicable", account_status: "active", assisted_kind: null, team_allocation: { visibility: "allowed", status: "pending", version: 1, can_update: true } })), total: 2, page: 1, page_size: 20 })
    if (path === "/assets/categories") return json({ groups: [{ key: "computers", label: "电脑与电竞设备", options: ["台式电脑", "显示器"] }], legacy_categories: [] })
    if (path === "/assets") return json({ items: [{ id, asset_code: "UI-001", name: "测试训练电脑", category: "台式电脑", brand: "测试品牌", model: null, serial_number: null, status: "available", location: "测试训练室", purchase_date: null, warranty_expires_on: null, source_purchase_id: null, note: null, created_at: now, updated_at: now, photo: null, current_assignment: null, ownership_type: "rented", rental_started_on: "2026-01-01", rental_expires_on: "2026-12-31", rental_returned_on: null, rental_revision: 1 }], page: 1, page_size: 50, total: 1 })
    if (path === "/procurement/settlement-centers") return json([{ id, name: "测试运营中心", is_active: true, reserve_fund_total_cents: 100000, reserve_fund_used_cents: 20000, reserve_fund_remaining_cents: 80000, created_at: now, updated_at: now }])
    if (path === "/procurement") return json({ items: [{ id, purchase_date: "2026-10-27", item_name: "测试饮用水", purchase_channel: "offline", product_url: null, quantity: "10.000", unit_price_cents: 1800, total_price_cents: 18000, settlement_center_id: id, settlement_center_name: "测试运营中心", uses_reserve_fund: false, is_reimbursed: false, is_settled: false, settled_on: null, has_invoice: false, invoice: null, note: "合成采购记录", created_at: now, updated_at: now }], page: 1, page_size: 50, total: 1, total_amount_cents: 18000, reimbursed_amount_cents: 0, unreimbursed_amount_cents: 18000, settled_amount_cents: 0, unsettled_amount_cents: 18000 })
    if (path === "/revenue-share/finance") return json({ earned_cents: 10000, settled_cents: 2000, outstanding_cents: 8000, pending_payment_count: 0, items: [{ beneficiary_kind: "person", beneficiary_id: id, beneficiary_name: "测试教练", earned_cents: 10000, settled_cents: 2000, outstanding_cents: 8000 }] })
    if (path === "/operations/overview") return json(operations)
    if (["/operations/history", "/operations/alerts", "/operations/request-errors"].includes(path)) return json({ items: [], total: 0, page: 1, page_size: path.endsWith("alerts") ? 10 : 20, retention_days: 30 })
    if (path === "/operations/backup-status") return json({ status: "unknown", completed_at: null, last_success_at: null, started_at: null, verification_at: null, verification_status: "unverified", size_bytes: null, error_code: null, reason: "合成验收数据，无真实备份信息", secondary_copy: { configured: false, status: "not_configured", last_success_at: null } })
    if (path === "/operations/cloud-backups") return json({ targets: [], jobs: [], worker_last_seen_at: null })
    if (path === "/registration/channels") return json({ email_enabled: true, phone_enabled: false })
    unexpected.push(`GET ${path}`)
    return json({ status: 404, detail: "Missing synthetic UI fixture" }, 404)
  })
  return { unexpected, mutations, setAppearance(next: "clear" | "command") { appearance = { theme: next, revision: appearance.revision + 1 } } }
}
