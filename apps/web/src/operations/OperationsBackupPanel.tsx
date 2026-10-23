import { useQuery } from "@tanstack/react-query"
import { formatAuditDateTime } from "../shared/date"
import { getOperationsBackupStatus } from "./api"
import { OperationsCloudBackupPanel } from "./OperationsCloudBackupPanel"

const statusLabels = { running: "正在备份", ok: "备份成功", failed: "备份异常", unknown: "等待备份状态" }

export function OperationsBackupPanel({ autoRefresh }: { autoRefresh: boolean }) {
  const query = useQuery({
    queryKey: ["operations", "backup-status"], queryFn: getOperationsBackupStatus,
    refetchInterval: autoRefresh ? 30_000 : false,
  })
  const data = query.data
  return <section className="operationsPanel operationsBackupPanel" aria-label="自动备份与恢复">
    <div className="operationsPanelHeading"><h2>自动备份与恢复</h2><button type="button" disabled={query.isFetching} onClick={() => void query.refetch()}>刷新备份状态</button></div>
    {query.isPending ? <p>正在读取备份状态…</p> : query.isError || !data ? <p className="formError" role="alert">备份状态读取失败，请重试。</p> : <>
      <div className="operationsBackupFacts">
        <div><span>最近执行</span><strong className={data.status === "failed" ? "formError" : ""}>{statusLabels[data.status]}</strong><small>{data.reason}</small></div>
        <div><span>最近成功备份</span><strong>{data.last_success_at ? formatAuditDateTime(data.last_success_at) : "暂无成功记录"}</strong><small>{data.size_bytes === null ? "" : `备份大小 ${(data.size_bytes / 1024 / 1024).toFixed(2)} MB`}</small></div>
        <div><span>隔离恢复验证</span><strong>{data.verification_status === "verified" ? "已通过" : "待验证"}</strong><small>{data.verification_at ? formatAuditDateTime(data.verification_at) : "暂无验证记录"}</small></div>
        <div><span>自动复制副本</span><strong>{!data.secondary_copy.configured ? "未配置" : data.secondary_copy.status === "ok" ? "复制成功" : data.secondary_copy.status === "failed" ? "复制异常" : "等待状态"}</strong><small>{data.secondary_copy.last_success_at ? formatAuditDateTime(data.secondary_copy.last_success_at) : "可在备份配置中指定独立存储设备"}</small></div>
      </div>
      <p className="operationsHelp">恢复验证会在隔离环境还原备份并核对数据，不会覆盖当前业务。只有备份成功且验证通过，才会更新成功记录。</p>
    </>}
    <OperationsCloudBackupPanel autoRefresh={autoRefresh} />
  </section>
}
