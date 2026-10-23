import type {
  AssignmentType,
  AutomationIssue,
  ClassType,
  CohortStatus,
  TransitionTrigger,
} from "./api"

export const COHORT_STATUS_LABELS: Record<CohortStatus, string> = {
  draft: "草稿",
  open: "开放报名",
  upcoming: "即将开营",
  active: "进行中",
  completed: "已结营",
  archived: "已归档",
}

export const AUTOMATION_ISSUE_LABELS: Record<AutomationIssue, string> = {
  auto_start_transition_blocked: "自动开营受阻",
  auto_complete_transition_blocked: "自动结营受阻",
}

export const TRIGGER_LABELS: Record<TransitionTrigger, string> = {
  manual: "管理员操作",
  scheduler: "系统定时",
  request_reconciliation: "请求校准",
}

export const CLASS_TYPE_LABELS: Record<ClassType, string> = {
  standard: "普通班",
  elite: "菁英班",
}

export const ASSIGNMENT_TYPE_LABELS: Record<AssignmentType, string> = {
  primary: "主教练",
  assistant: "助教",
}
