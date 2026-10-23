import { useQuery } from "@tanstack/react-query"

import { useAuth } from "../auth/AuthProvider"
import { useEvaluationAuthorization } from "./EvaluationAuthorization"
import {
  getSystemEvaluationTaskFilterOptions,
  getSystemEvaluationTask,
  getSystemEvaluationTaskWorkspace,
  listSystemEvaluationTasks,
  type SystemEvaluationTaskDetail,
  type SystemEvaluationTaskListInput,
  type SystemEvaluationTaskPage,
} from "./systemEvaluationApi"
import {
  systemEvaluationKeys,
  type SystemEvaluationProjection,
  type SystemEvaluationTaskStatus,
} from "./systemEvaluationKeys"

export const SYSTEM_EVALUATION_REFETCH_INTERVAL_MS = 5_000

const terminalStatuses = new Set<SystemEvaluationTaskStatus>(["published", "archived"])

export function systemEvaluationRefetchInterval(
  data: SystemEvaluationTaskPage | undefined,
  visibilityState: DocumentVisibilityState = document.visibilityState,
): number | false {
  if (visibilityState !== "visible") return false
  return data?.items.some((task) => !terminalStatuses.has(task.status))
    ? SYSTEM_EVALUATION_REFETCH_INTERVAL_MS
    : false
}

export function systemEvaluationTaskRefetchInterval(
  data: SystemEvaluationTaskDetail | undefined,
  visibilityState: DocumentVisibilityState = document.visibilityState,
): number | false {
  if (visibilityState !== "visible" || !data) return false
  return terminalStatuses.has(data.task.status)
    ? false
    : SYSTEM_EVALUATION_REFETCH_INTERVAL_MS
}

export function useSystemEvaluationTaskList(
  projection: SystemEvaluationProjection,
  filters: SystemEvaluationTaskListInput,
) {
  const { user } = useAuth()
  const authorization = useEvaluationAuthorization()
  const accountId = user?.account_id ?? "anonymous"
  return useQuery({
    queryKey: [
      ...systemEvaluationKeys.list(accountId, projection, filters),
      "authorization",
      authorization.revision,
    ] as const,
    queryFn: () => listSystemEvaluationTasks(projection, filters),
    enabled: Boolean(user?.account_id) && !authorization.pending,
    staleTime: 0,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchInterval: (query) => systemEvaluationRefetchInterval(query.state.data),
    refetchIntervalInBackground: false,
  })
}

export function useSystemEvaluationTaskFilterOptions(
  projection: SystemEvaluationProjection,
) {
  const { user } = useAuth()
  const authorization = useEvaluationAuthorization()
  const accountId = user?.account_id ?? "anonymous"
  return useQuery({
    queryKey: [
      ...systemEvaluationKeys.filterOptions(accountId, projection),
      "authorization",
      authorization.revision,
    ] as const,
    queryFn: () => getSystemEvaluationTaskFilterOptions(projection),
    enabled: Boolean(user?.account_id) && !authorization.pending,
    staleTime: 0,
    gcTime: 0,
    refetchOnWindowFocus: false,
  })
}

export function useSystemEvaluationTaskDetail(
  projection: SystemEvaluationProjection,
  taskId: string,
) {
  const { user } = useAuth()
  const authorization = useEvaluationAuthorization()
  const accountId = user?.account_id ?? "anonymous"
  return useQuery({
    queryKey: [
      ...systemEvaluationKeys.detail(accountId, projection, taskId),
      "authorization",
      authorization.revision,
    ] as const,
    queryFn: () => getSystemEvaluationTask(projection, taskId),
    enabled: Boolean(user?.account_id) && Boolean(taskId) && !authorization.pending,
    staleTime: 0,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchInterval: (query) =>
      systemEvaluationTaskRefetchInterval(query.state.data),
    refetchIntervalInBackground: false,
  })
}

export function useSystemEvaluationTaskWorkspace(
  projection: SystemEvaluationProjection,
  taskId: string,
) {
  const { user } = useAuth()
  const authorization = useEvaluationAuthorization()
  const accountId = user?.account_id ?? "anonymous"
  return useQuery({
    queryKey: [
      ...systemEvaluationKeys.workspace(accountId, projection, taskId),
      "authorization",
      authorization.revision,
    ] as const,
    queryFn: () => getSystemEvaluationTaskWorkspace(projection, taskId),
    enabled: Boolean(user?.account_id) && Boolean(taskId) && !authorization.pending,
    staleTime: 0,
    gcTime: 0,
    refetchOnWindowFocus: false,
  })
}
