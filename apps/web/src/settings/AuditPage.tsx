import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import type { FormEvent, ReactNode } from "react"

import { ProblemDetailsError } from "../api/client"
import { useOptionalAuth } from "../auth/AuthProvider"
import { formatAuditDateTime, formatDateOnly } from "../shared/date"
import { listAuditEvents } from "./api"
import type { AuditFilters, AuditMetadata, AuditOutcome } from "./api"
import "./settings.css"

const phonePattern = /(?<!\d)1(?:[^\dA-Za-z]{0,3}\d){10}(?!\d)/
const identityPattern = /(?<!\d)(?:\d[^\dA-Za-z]{0,3}){17}[0-9Xx](?![0-9A-Za-z])/
const secretAssignmentPattern =
  /(?:password|temporary[_ -]?password|token|bearer|csrf|cipher(?:text)?|密码|临时密码|令牌)\s*(?::|：|=|\s)\s*\S{8,}/i
const rawSecretPattern = /(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])/g
const canonicalUuidPattern =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

function safeAuditText(value: string | null | undefined): string {
  if (!value) return "未填写"
  const rawSecret = [...value.matchAll(rawSecretPattern)].some((match) => {
    const candidate = match[0]
    const classes = [
      /[a-z]/.test(candidate),
      /[A-Z]/.test(candidate),
      /\d/.test(candidate),
      /[_-]/.test(candidate),
    ].filter(Boolean).length
    return classes >= 3
  })
  if (
    phonePattern.test(value) ||
    identityPattern.test(value) ||
    secretAssignmentPattern.test(value) ||
    (!canonicalUuidPattern.test(value) && rawSecret)
  ) {
    return "敏感内容已隐藏"
  }
  return value
}

function nextCalendarDate(value: string): string {
  const [year, month, day] = value.split("-").map(Number)
  const next = new Date(Date.UTC(year, month - 1, day + 1))
  return next.toISOString().slice(0, 10)
}

const actionLabels: Record<string, string> = {
  "reactionx.pairing.create": "创建 ReactionX 配对",
  "reactionx.pairing.claim": "领取 ReactionX 配对",
  "reactionx.pairing.confirm_browser": "网页确认 ReactionX 配对",
  "reactionx.pairing.confirm_device": "设备确认 ReactionX 配对",
  "reactionx.pairing.activate": "激活 ReactionX 连接器",
  "reactionx.pairing.cancel": "取消 ReactionX 配对",
  "reactionx.connector.revoke": "撤销 ReactionX 连接器",
  "reactionx.run.prepare": "准备 ReactionX 训练",
  "reactionx.run.start": "启动 ReactionX 训练",
  "reactionx.run.stop": "请求停止 ReactionX 训练",
  "reactionx.run.safety_stop": "安全停止 ReactionX 训练",
  "reactionx.run.stop_confirmed": "确认 ReactionX 灯具已停止",
  "reactionx.run.manual_activate": "手动激活 ReactionX 灯具",
  "reactionx.device.scan": "扫描 ReactionX 灯具",
  "reactionx.device.connect": "连接 ReactionX 灯具",
  "reactionx.device.disconnect": "断开 ReactionX 灯具",
  "reactionx.device.identify": "识别 ReactionX 灯具",
  "reactionx.result.saved": "保存 ReactionX 结果",
  "reactionx.upload.authorized": "授权 ReactionX 原包补传",
  "reactionx.recovery.requested": "申请 ReactionX 结果恢复",
  "reactionx.recovery.confirmed": "二次确认 ReactionX 结果恢复",
  "referral_organization_manager.assign": "分配邀请机构负责人",
  "referral_organization_manager.revoke": "移除邀请机构负责人",
  "referral_organization_manager.list": "查看邀请机构负责人",
  "referral_organization_manager.candidates": "查询负责人候选账号",
  "referrals.housing.read": "查看受邀人员住宿",
  "referrals.sources.read": "查看邀请来源",
  "team_memberships.join": "加入队伍",
  "team_memberships.leave": "退出队伍",
  "team_memberships.transfer": "调动队伍",
  "team_managers.assign": "分配队伍管理人员",
  "team_managers.unassign": "解除队伍管理人员分配",
  "team_staff.assign": "分配队内只读岗位",
  "team_staff.unassign": "解除队内只读岗位",
  "operations.alert.acknowledge": "确认运维告警已知晓",
  "operations.appearance.update": "修改全局系统外观",
  "account.create": "创建账号",
  "account.delete": "删除账号",
  "account.disable": "停用账号",
  "account.enable": "启用账号",
  "account.password.reset": "重置账号密码",
  "account.permission_override.add": "新增账号权限规则",
  "account.permission_override.remove": "移除账号权限规则",
  "account.role.assign": "分配账号角色",
  "account.role.remove": "移除账号角色",
  "account.job_role.change": "变更账号岗位",
  "person.create": "创建人员档案",
  "person.update": "修改人员档案",
  "person.assignments.update": "修改人员身份与项目教练归属",
  "person.nickname.update": "修改个人昵称",
  "person.profile.update": "修改人员昵称与备注",
  "housing.stay.update_planned_departure": "修改计划退宿日期",
  "housing.stay.void_history": "作废历史住宿",
  "person.delete": "删除人员档案",
  "person.identity.reveal": "查看人员实名信息",
  "guardian.relationship.create": "建立监护关系",
  "guardian.relationship.end": "解除监护关系",
  "student_game_account.bind": "绑定学员游戏账号",
  "student_game_account.unbind": "解除学员游戏账号",
  "student_game_account.limit_update": "修改游戏账号绑定上限",
  "housing.building.create": "创建住宿楼栋",
  "housing.room.verify_batch": "批量确认房间",
  "housing.room.service_status_batch.available": "批量设为可用",
  "housing.room.service_status_batch.maintenance": "批量设为维修",
  "housing.room.service_status_batch.disabled": "批量设为停用",
  "housing.room.update": "修改房间",
  "housing.room_card.save": "修改房卡日期",
  "housing.property_change.submit": "提交物业房态变更",
  "housing.property_change.review": "审核物业房态变更",
  "housing.property_change.withdraw": "撤回物业房态变更",
  "housing.property.export": "导出住宿与问题房清单",
  "housing.room.cleaning.complete": "完成房间保洁",
  "housing.room_photo.put": "上传房间照片",
  "housing.room_photo.delete": "删除房间照片",
  "housing.room_photo.read": "查看房间照片",
  "housing.room_status.export": "导出房态快照",
  "housing.report_batch.correct": "修正住宿实名报送",
  "housing.report_batch.mark_submitted": "标记住宿实名报送",
  "housing.report_batch.void": "作废住宿实名报送",
  "mail.settings.update": "修改邮件发送设置",
  "mail.settings.test": "测试邮件发送设置",
  "mail.template.update": "修改邮件模板",
  "mail.template.restore_default": "恢复默认邮件模板",
  "registration.channel_settings.update": "修改注册渠道设置",
  "staff_identity_application.approve": "通过工作人员身份申请",
  "staff_identity_application.reject": "驳回工作人员身份申请",
  "food_preferences.cuisine.create": "新增菜系",
  "food_preferences.cuisine.update": "修改菜系",
  "food_preferences.profile.update": "修改饮食偏好",
  "food_preferences.onboarding.complete": "完成饮食偏好登记",
  "teaching.project.create": "创建教学项目",
  "teaching.project.update": "修改教学项目",
  "teaching.cohort.create": "创建训练营期",
  "teaching.cohort.update": "修改训练营期",
  "teaching.cohort.transition": "变更训练营期状态",
  "teaching.class.create": "创建教学班级",
  "teaching.class.update": "修改教学班级",
  "teaching.coach_assignment.create": "分配项目教练",
  "teaching.coach_assignment.end": "结束项目教练分配",
  "admissions.payment.create_manual": "手动登记付款",
  "admissions.payment.refund": "办理付款退款",
  "admissions.enrollment.confirm": "确认学员报名",
  "admissions.enrollment.cancel": "取消学员报名",
  "admissions.enrollment.transfer": "转移学员报名",
  "admissions.enrollment.schedule.update": "调整学员参加时间",
  "admissions.enrollment.withdrawal_refund": "办理退营退款",
  "admissions.payment_import.process": "处理付款导入",
  "asset.export": "导出资产台账",
  "asset.rental.renew": "续期资产租约",
  "asset.rental.return": "资产退租",
  "asset.photo.upload": "上传资产辨识照片",
  "asset.photo.remove": "移除资产辨识照片",
  "procurement.center.create": "新增采购结算中心",
  "procurement.center.update": "修改采购结算中心",
  "procurement.center.reserve_fund.add": "增加结算中心备用金",
  "procurement.purchase.create": "新增日常采购",
  "procurement.purchase.update": "修改日常采购",
  "procurement.purchase.reimbursement": "变更采购报销状态",
  "procurement.purchase.settlement": "变更采购结算状态",
  "procurement.purchase.export": "导出采购台账",
  "procurement.invoice.upload": "上传并关联采购发票",
  "procurement.invoice.remove": "移除采购发票",
  "travel_report.import": "导入并转换订票账单",
  "travel_report.source.download": "下载订票原始账单",
  "travel_report.converted.download": "下载订票转换报表",
  "evaluation.task.create": "创建系统评价任务",
  "evaluation.task.archived": "归档系统评价任务",
  "evaluation.task.result_submitted": "提交系统评价结果",
  "evaluation.task.reopened": "重新打开系统评价任务",
  "student_report.submit": "提交学员教学评语",
  "student_report.review": "审核学员教学评语",
  "student_report.reopen": "重新打开学员教学评语",
}

const actionSegmentLabels: Record<string, string> = {
  account: "账号",
  accounts: "账号",
  admissions: "招生报名",
  admission: "报名",
  auth: "登录认证",
  camp: "训练营",
  camp_surveys: "训练营问卷",
  cohort: "训练营期",
  cohort_staff_assignment: "训练营人员分配",
  evaluation: "系统评价",
  evaluation_staff_role: "评价人员角色",
  food_preferences: "饮食偏好",
  guardian: "监护关系",
  housing: "住宿",
  asset: "资产",
  mail: "邮件",
  person: "人员档案",
  procurement: "日常采购",
  travel_report: "订票报表",
  portal: "学员门户",
  registration: "注册",
  staff_identity_application: "工作人员身份申请",
  student_game_account: "学员游戏账号",
  student_report: "学员教学评语",
  teaching: "教学",
}

const operationLabels: Record<string, string> = {
  add: "新增",
  approve: "通过",
  archive: "归档",
  assign: "分配",
  bind: "绑定",
  cancel: "取消",
  certify: "认证",
  close: "关闭",
  complete: "完成",
  create: "创建",
  delete: "删除",
  disable: "停用",
  enable: "启用",
  end: "结束",
  list: "查询",
  match: "匹配",
  process: "处理",
  read: "查看",
  reject: "驳回",
  remove: "移除",
  reopen: "重新打开",
  replace_permissions: "替换权限",
  reset: "重置",
  review: "审核",
  submit: "提交",
  test: "测试",
  transfer: "转移",
  unbind: "解除绑定",
  update: "修改",
  withdrawal_refund: "办理退营退款",
}

const resourceLabels: Record<string, string> = {
  housing_room_card: "住宿房卡",
  housing_property_change: "物业房态变更申请",
  housing_property_export: "住宿与问题房清单",
  referral_organization_manager: "邀请机构负责人关系",
  invited_housing: "受邀人员住宿",
  invitation_source: "邀请来源",
  cloud_backup: "云备份",
  team_membership: "队伍成员关系",
  team_manager_assignment: "队伍管理关系",
  team_staff_assignment: "队内工作人员关系",
  operations_alert: "运维告警",
  global_appearance: "全局系统外观",
  account: "账号",
  admission_case: "招生报名记录",
  coach_assignment: "教练分配记录",
  cohort: "训练营期",
  cohort_staff_assignment: "训练营人员分配",
  evaluation_adapter_certification: "评价适配器认证",
  evaluation_media_asset: "评价媒体",
  evaluation_staff_role: "评价人员角色",
  evaluation_bridge_request: "评价桥接请求",
  evaluation_bridge_ticket: "评价桥接凭证",
  food_cuisine_option: "菜系",
  food_preference_export: "饮食偏好导出",
  game_project: "游戏项目",
  guardian_relationship: "监护关系",
  housing_building: "住宿楼栋",
  housing_floor: "住宿楼层",
  housing_report_batch: "住宿实名报送批次",
  housing_request: "住宿申请",
  housing_room: "房间",
  housing_room_issue: "问题房间记录",
  housing_stay: "住宿记录",
  asset: "资产",
  asset_export: "资产导出",
  procurement_settlement_center: "采购结算中心",
  daily_purchase_record: "日常采购记录",
  daily_purchase_export: "采购台账导出",
  procurement_invoice: "采购发票",
  travel_report_conversion: "订票报表转换记录",
  mail_settings: "邮件发送设置",
  mail_template: "邮件模板",
  payment: "付款记录",
  payment_conflict: "付款冲突记录",
  payment_import_event: "付款导入事件",
  person: "人员档案",
  person_food_preference: "人员饮食偏好",
  professional_team: "职业战队",
  registration_application: "注册申请",
  registration_channel_settings: "注册渠道设置",
  service_account: "付款服务账号",
  service_account_credential: "付款服务凭据",
  staff_identity_application: "工作人员身份申请",
  student_auxiliary_phone: "学员备用手机号",
  student_game_account: "学员游戏账号",
  student_profile: "学员档案",
  student_report: "学员教学评语",
  system_evaluation_task: "系统评价任务",
  teaching_class: "教学班级",
}

const roleLabels: Record<string, string> = {
  property: "物业",
  team_member: "队员",
  team_manager: "队伍管理",
  admin: "管理员",
  administration: "行政",
  coach: "教练",
  assistant_coach: "助教",
  customer_service: "客服",
  finance: "财务",
  guardian: "监护人",
  life_teacher: "生活老师",
  registered_user: "已注册用户",
  staff: "工作人员",
  student: "学员",
  super_admin: "超级管理员",
}

const permissionLabels: Record<string, string> = {
  "housing.changes.submit": "提交房态变更申请",
  "housing.changes.review": "审核房态变更申请",
  "housing.keycards.manage": "维护房卡日期",
  "housing.exports.basic": "导出住宿与问题房清单",
  "referrals.read_invited_housing": "查看受邀人员住宿",
  "team_memberships.read": "查看队伍成员关系",
  "team_memberships.manage": "管理队伍成员关系",
  "team_memberships.transfer": "调动队伍成员",
  "team_manager_assignments.manage": "分配队伍管理人员",
  "accounts.manage": "管理账号",
  "accounts.read": "查看账号列表",
  "assets.export": "导出资产台账",
  "assets.manage": "管理资产台账",
  "assets.read": "查看资产台账",
  "assets.read_self": "查看本人资产",
  "audit.read": "查看安全审计",
  "coach_roster.read": "查看教练下一期名单",
  "evaluation_center.access": "进入评价中心",
  "evaluation_jobs.review": "审核系统评价",
  "food_preferences.catalog.manage": "管理菜系字典",
  "food_preferences.export": "导出饮食偏好统计",
  "housing.read": "查看房态",
  "housing.rooms.manage": "管理房间目录",
  "mail.settings.manage": "管理邮件发送设置",
  "people.create": "创建人员",
  "people.read": "查看人员档案",
  "people.update": "更新人员",
  "permissions.manage": "管理角色与权限",
  "procurement.read": "查看日常采购",
  "procurement.manage": "管理日常采购",
  "procurement.settle": "管理采购结算",
  "procurement.export": "导出日常采购",
  "procurement.centers.manage": "管理采购结算中心",
  "procurement.invoices.manage": "管理采购发票",
  "travel_reports.read": "查看订票报表",
  "travel_reports.import": "导入订票账单",
  "travel_reports.export": "下载订票报表",
  "registration.review": "审核注册冲突",
  "staff_identity_applications.review": "审核工作人员身份申请",
  "student_game_accounts.manage": "管理学员游戏账号",
  "student_reports.review": "审核学员日报和周报",
  "student_reports.write": "填写学员日报和周报",
  "teaching.read": "查看教学配置",
}

const scopeLabels: Record<string, string> = {
  assigned_team: "指定队伍",
  assigned_class: "指定班级",
  building: "指定楼栋",
  cohort: "指定训练营期",
  enrollment: "指定报名记录",
  family: "本人家庭",
  floor: "指定楼层",
  global: "全部数据",
  person: "指定人员",
  project: "指定项目",
  self: "本人",
}

const metadataKeyLabels: Record<string, string> = {
  previous_cleaning_frequency: "原保洁需求",
  cleaning_frequency: "保洁需求",
  previous_card_issued_on: "原房卡开卡日期",
  card_issued_on: "房卡开卡日期",
  previous_card_expires_on: "原房卡到期日期",
  card_expires_on: "房卡到期日期",
  housing_change_decision: "房态审核决定",
  housing_export_kind: "清单类型",
  old_role_keys: "原账号角色",
  new_role_keys: "新账号角色",
  keep_concurrent_roles: "保留其他兼任岗位",
  old_theme: "原主题",
  new_theme: "新主题",
  old_version: "原版本",
  ownership_type: "资产归属",
  acquisition_method: "取得方式",
  acquisition_channel: "采购渠道",
  acquisition_note_changed: "获得方式说明已修改",
  rental_started_on: "租赁开始日期",
  rental_expires_on: "租赁到期日期",
  rental_returned_on: "退租日期",
  rental_revision: "租期版本",
  rental_period: "续期周期",
  previous_expires_on: "原到期日期",
  previous_daily_cleaning_enabled: "原每日保洁",
  daily_cleaning_enabled: "新每日保洁",
  changed_fields: "修改字段",
  old_nickname: "原昵称",
  new_nickname: "新昵称",
  previous_duration_mode: "原住宿期限",
  duration_mode: "新住宿期限",
  previous_planned_check_out_at: "原计划退宿日期",
  planned_check_out_at: "新计划退宿日期",
  organization_id: "邀请机构编号",
  target_account_id: "目标账号编号",
  source_kind: "邀请来源类型",
  operation_id: "操作编号",
  membership_id: "成员关系编号",
  closed_membership_id: "已结束成员关系编号",
  assignment_id: "管理关系编号",
  source_team_id: "原队伍编号",
  target_team_id: "目标队伍编号",
  person_id: "人员编号",
  new_version: "更新后版本",
  professional_team_id: "队伍编号",
  is_team_division: "分部用途",
  affected_account_count: "受影响账号数",
  attempt: "尝试次数",
  attempt_status: "尝试状态",
  batch_id: "批次编号",
  client_id: "客户端",
  conflict_code: "冲突类型",
  error_code: "错误类型",
  field: "字段",
  file_sha256: "文件校验值",
  effect: "权限结果",
  export_scope: "导出范围",
  format: "导出格式",
  settlement_center_id: "结算中心编号",
  include_inactive: "包含停用人员",
  is_reimbursed: "是否已报销",
  uses_reserve_fund: "是否使用备用金",
  item_count: "项目数量",
  mode: "操作模式",
  new_limit: "新上限",
  new_status: "新状态",
  old_limit: "原上限",
  old_status: "原状态",
  permission_key: "权限能力",
  population: "统计范围",
  profile_version: "资料版本",
  purpose: "用途",
  refund_type: "退款方式",
  report_kind: "评语类型",
  report_status: "评语状态",
  result_count: "结果数量",
  result_version: "结果版本",
  review_decision: "审核决定",
  role_key: "角色",
  scope_id: "范围编号",
  scope_kind: "数据范围",
  source: "数据来源",
  state: "当前状态",
  task_status: "任务状态",
  transition_trigger: "状态变更来源",
}

const enumValueLabels: Record<string, string> = {
  allow: "允许",
  deny: "拒绝",
  decrypt_failed: "解密失败",
  hidden: "不显示实名",
  real_name: "显示实名",
  manual: "手动操作",
  scheduler: "系统定时任务",
  request_reconciliation: "请求校准",
  withdrawal_prorated: "按剩余日期退款",
  withdrawal_full: "全额退款",
  password_reset: "密码重置",
  current: "当前筛选结果",
  all: "全部记录",
  unreimbursed: "待报销",
  reimbursed: "已报销",
  unsettled: "待结算",
  settled: "已结算",
}

function labelAction(action: string): string {
  const direct = actionLabels[action]
  if (direct) return direct
  const cloudAction = /^operations\.cloud_backup\.(baidu|115|quark)\.(update|test|copy)$/.exec(action)
  if (cloudAction) {
    const providerLabels: Record<string, string> = { baidu: "百度网盘", "115": "115 网盘", quark: "夸克网盘" }
    const cloudOperations: Record<string, string> = { update: "更新云备份配置", test: "测试云备份连接", copy: "复制云备份" }
    return `${providerLabels[cloudAction[1]]}：${cloudOperations[cloudAction[2]]}`
  }
  const parts = action.split(".")
  const subject = parts
    .slice(0, -1)
    .map((part) => actionSegmentLabels[part])
    .filter(Boolean)
    .join("")
  const operation = operationLabels[parts.at(-1) ?? ""]
  if (subject && operation) return `${operation}${subject}`
  if (subject) return `${subject}操作`
  return "其他管理操作"
}

function labelResource(resourceType: string): string {
  return resourceLabels[resourceType] ?? "其他业务对象"
}

function labelPermission(permissionKey: string): string {
  const direct = permissionLabels[permissionKey]
  if (direct) return direct
  const parts = permissionKey.split(".")
  const subject = parts
    .slice(0, -1)
    .map((part) => actionSegmentLabels[part])
    .filter(Boolean)
    .join("")
  const operation = operationLabels[parts.at(-1) ?? ""]
  return subject && operation ? `${operation}${subject}` : "权限能力"
}

function labelPurpose(value: string | null): string {
  if (!value) return "未填写"
  if (/^[A-Za-z0-9_.-]+$/.test(value)) {
    return enumValueLabels[value] ?? "已填写用途"
  }
  return safeAuditText(value)
}

function metadataValueLabel(key: string, value: unknown): string {
  if (key === "old_theme" || key === "new_theme") {
    return value === "clear" ? "清爽专业" : value === "command" ? "深蓝效率" : "其他主题"
  }
  if (["ownership_type", "acquisition_method", "acquisition_channel", "rental_period"].includes(key)) {
    if (value === null) return "未设置"
    const labels: Record<string, string> = { owned: "自有", rented: "租用", purchase: "采购", other: "其他", online: "线上", offline: "线下", week: "一周", month: "一月", quarter: "一季" }
    return typeof value === "string" ? labels[value] ?? "已记录" : "已记录"
  }
  if (["rental_started_on", "rental_expires_on", "rental_returned_on", "previous_expires_on", "previous_card_issued_on", "card_issued_on", "previous_card_expires_on", "card_expires_on"].includes(key)) {
    if (value === null) return "未设置"
    return typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) ? value : "日期无效"
  }
  if (key === "changed_fields" && Array.isArray(value)) {
    const fields: Record<string, string> = { nickname: "昵称", profile_notes: "个人档案备注" }
    return value.map((field) => typeof field === "string" ? fields[field] ?? "其他字段" : "其他字段").join("、")
  }
  if (key === "previous_duration_mode" || key === "duration_mode") {
    return value === "fixed_term" ? "固定期限" : value === "long_term" ? "长住" : "已记录"
  }
  if (key === "previous_planned_check_out_at" || key === "planned_check_out_at") {
    if (value === null) return "未设置"
    if (typeof value !== "string") return "时间无效"
    try { return formatDateOnly(value) } catch { return "时间无效" }
  }
  if ((key === "old_nickname" || key === "new_nickname") && value === null) return "未设置"
  if (typeof value === "boolean") return value ? "是" : "否"
  if (typeof value === "number") return String(value)
  if (Array.isArray(value)) {
    if (key === "old_role_keys" || key === "new_role_keys") return value.map(role => typeof role === "string" ? roleLabels[role] ?? "自定义角色" : "未知角色").join("、") || "无"
    return `${value.length} 项`
  }
  if (typeof value !== "string") return "已记录"
  if (key === "source_kind") return value === "organization" ? "机构" : value === "person" ? "个人" : "其他来源"
  if (key === "field") {
    return value === "identity_number" ? "身份证号" : value === "phone" ? "手机号" : "其他字段"
  }
  if (key === "role_key") return roleLabels[value] ?? "自定义角色"
  if (key === "previous_cleaning_frequency" || key === "cleaning_frequency") {
    return ({ unspecified: "未设置", daily: "每日保洁", weekly: "每周保洁", on_demand: "按需保洁" } as Record<string, string>)[value] ?? "未知保洁需求"
  }
  if (key === "housing_change_decision") return value === "approve" ? "通过" : value === "reject" ? "驳回" : "未知决定"
  if (key === "housing_export_kind") return value === "occupants" ? "住宿人员列表" : value === "problems" ? "问题房清单" : "未知清单"
  if (key === "permission_key") return labelPermission(value)
  if (key === "scope_kind") return scopeLabels[value] ?? "其他范围"
  if (key === "effect") return enumValueLabels[value] ?? "已记录"
  if (key === "export_scope") return enumValueLabels[value] ?? "其他范围"
  if (key === "error_code" || key === "transition_trigger" || key === "refund_type") {
    return enumValueLabels[value] ?? "已记录"
  }
  if (key === "old_status" || key === "new_status") {
    return (
      enumValueLabels[value] ??
      (value === "active" ? "启用" : value === "disabled" ? "停用" : "已记录")
    )
  }
  if (key === "population") return value === "all_people" ? "所有人员" : "学员"
  if (key === "mode") return enumValueLabels[value] ?? "已记录"
  return safeAuditText(value)
}

function metadataItems(metadata: AuditMetadata): ReactNode[] {
  return Object.entries(metadata).flatMap(([key, value]) => {
    if (["reason", "transfer_reason", "profile_notes", "acquisition_note", "binary", "request_body", "photo_bytes", "image_bytes", "image_data", "file_content"].includes(key)) return []
    if (key === "previous_rental_facts" || key === "rental_facts") {
      if (!value || typeof value !== "object" || Array.isArray(value)) return []
      const facts = value as Record<string, unknown>
      return ["ownership_type", "acquisition_method", "acquisition_channel", "rental_started_on", "rental_expires_on", "rental_returned_on", "rental_revision"].flatMap((field) => {
        if (!(field in facts)) return []
        return [<span key={`${key}.${field}`}>{key === "previous_rental_facts" ? "原" : "新"}{metadataKeyLabels[field]}：{metadataValueLabel(field, facts[field])}</span>]
      })
    }
    const explicitClear = ["old_nickname", "new_nickname", "previous_planned_check_out_at", "planned_check_out_at"].includes(key)
    if (value === undefined || (value === null && !explicitClear)) return []
    const label = metadataKeyLabels[key] ?? (key.endsWith("_id") ? "关联编号" : "变更信息")
    return [
      <span key={key}>
        {label}：{metadataValueLabel(key, value)}
      </span>,
    ]
  })
}

function AuditMetadataView({ metadata }: { metadata: AuditMetadata }) {
  const items = metadataItems(metadata)
  return <>{items.length > 0 ? items : <span>无公开变更信息</span>}</>
}

const outcomeLabels: Record<AuditOutcome, string> = {
  allowed: "已允许",
  denied: "已拒绝",
  failed: "执行失败",
}

function isAuthorizationDenied(error: unknown): boolean {
  return error instanceof ProblemDetailsError && error.problem.status === 403
}

export function AuditPage() {
  const queryClient = useQueryClient()
  const auth = useOptionalAuth()
  const [actorUserId, setActorUserId] = useState("")
  const [action, setAction] = useState("")
  const [resourceType, setResourceType] = useState("")
  const [dateFrom, setDateFrom] = useState("")
  const [dateTo, setDateTo] = useState("")
  const [filters, setFilters] = useState<AuditFilters>({ page: 1, pageSize: 20 })
  const [accessRevoked, setAccessRevoked] = useState(false)
  const events = useQuery({
    queryKey: ["audit-events", filters],
    queryFn: () => listAuditEvents(filters),
    enabled: !accessRevoked,
    retry: false,
  })
  const explicitAuthorizationDenial = isAuthorizationDenied(events.error)
  const permissionRevoked = accessRevoked || explicitAuthorizationDenial

  useEffect(() => {
    if (!explicitAuthorizationDenial || accessRevoked) return
    setAccessRevoked(true)
    const queryKey = ["audit-events"] as const
    const clearSensitiveQueries = () => {
      void queryClient.cancelQueries({ queryKey })
      queryClient.removeQueries({ queryKey })
    }
    clearSensitiveQueries()
    if (auth?.user) {
      void auth.refreshUser({ background: true })
        .catch(() => undefined)
        .finally(clearSensitiveQueries)
    }
  }, [accessRevoked, auth, explicitAuthorizationDenial, queryClient])

  function search(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    setFilters({
      actorUserId: actorUserId.trim() || undefined,
      action: action.trim() || undefined,
      resourceType: resourceType.trim() || undefined,
      dateFrom: dateFrom ? `${dateFrom}T00:00:00+08:00` : undefined,
      dateTo: dateTo ? `${nextCalendarDate(dateTo)}T00:00:00+08:00` : undefined,
      page: 1,
      pageSize: 20,
    })
  }

  return (
    <div className="settingsPage auditPage">
      <div className="settingsHeading">
        <div>
          <p className="pageEyebrow">操作留痕</p>
          <h1>安全审计</h1>
          <p>查看操作人、修改内容和发生时间；页面只展示经过安全过滤的审计信息。</p>
        </div>
      </div>

      <form className="auditFilters" onSubmit={search}>
        <div className="formField">
          <label htmlFor="audit-actor">操作人账号编号</label>
          <input
            id="audit-actor"
            value={actorUserId}
            onChange={(event) => setActorUserId(event.target.value)}
          />
        </div>
        <div className="formField">
          <label htmlFor="audit-action">操作类型</label>
          <input
            id="audit-action"
            value={action}
            onChange={(event) => setAction(event.target.value)}
          />
        </div>
        <div className="formField">
          <label htmlFor="audit-resource">对象类型</label>
          <input
            id="audit-resource"
            value={resourceType}
            onChange={(event) => setResourceType(event.target.value)}
          />
        </div>
        <div className="formField">
          <label htmlFor="audit-date-from">开始日期</label>
          <input
            id="audit-date-from"
            type="date"
            value={dateFrom}
            onChange={(event) => setDateFrom(event.target.value)}
          />
        </div>
        <div className="formField">
          <label htmlFor="audit-date-to">结束日期</label>
          <input
            id="audit-date-to"
            type="date"
            value={dateTo}
            onChange={(event) => setDateTo(event.target.value)}
          />
        </div>
        <button className="primaryButton" type="submit">
          查询审计
        </button>
      </form>

      {permissionRevoked ? (
        <div className="settingsState" role="alert">
          安全审计权限已撤销，正在刷新当前账号权限。
        </div>
      ) : events.isLoading ? <div className="settingsState">正在读取审计记录…</div> : null}
      {!permissionRevoked && events.isError ? <div className="settingsState">暂时无法读取审计记录。</div> : null}
      {!permissionRevoked && events.data?.items.length === 0 ? <div className="settingsState">没有匹配记录。</div> : null}
      {!permissionRevoked && events.data?.items.length ? (
        <section className="settingsTable auditTable" role="table" aria-label="审计事件">
          <div className="settingsTableHeader auditRow" role="row">
            <span role="columnheader">操作人 / 时间 / 结果</span>
            <span role="columnheader">修改内容</span>
            <span role="columnheader">用途</span>
            <span role="columnheader">审计详情</span>
          </div>
          {events.data.items.map((event) => (
            <article className="settingsTableRow auditRow" role="row" key={event.id}>
              <div role="cell" data-label="操作人 / 时间 / 结果">
                <strong>{event.actor_name ?? (event.actor_user_id ? "未知操作人" : "系统任务")}</strong>
                <small>
                  {event.actor_user_id ? `账号编号：${safeAuditText(event.actor_user_id)}` : "系统自动记录"}
                </small>
                <small>
                  发生时间：{formatAuditDateTime(event.created_at)}
                </small>
                <span className={`statusPill ${event.outcome}`}>
                  {outcomeLabels[event.outcome]}
                </span>
              </div>
              <div className="auditChange" role="cell" data-label="修改内容">
                <strong>{labelAction(event.action)}</strong>
                <small>对象：{labelResource(event.resource_type)}</small>
                <small>
                  {event.resource_id
                    ? `目标编号：${safeAuditText(event.resource_id)}`
                    : "未关联具体对象"}
                </small>
              </div>
              <div role="cell" data-label="用途">
                {["team_memberships.transfer", "housing.stay.void_history"].includes(event.action) ? "未公开" : labelPurpose(event.purpose)}
              </div>
              <div className="auditMetadata" role="cell" data-label="审计详情">
                <span className="auditMetadataTitle">变更信息</span>
                <AuditMetadataView metadata={event.action.startsWith("reactionx.") ? {} : event.metadata} />
                <small>请求编号：{safeAuditText(event.request_id)}</small>
              </div>
            </article>
          ))}
        </section>
      ) : null}
    </div>
  )
}
