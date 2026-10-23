import { useState } from "react"
import type { HousingOccupancyExportInput } from "./api"
import { HousingDialogShell } from "./HousingDialogShell"
import { HousingOccupancyExportDialog } from "./HousingOccupancyExportDialog"
import { HousingRoomStatusExportDialog } from "./HousingRoomStatusExportDialog"
import type { SnapshotDialogProps } from "./HousingRoomStatusExportDialog"

export function HousingExportDialog({ onSnapshot, onHistory, ...props }: Omit<SnapshotDialogProps, "onSubmit" | "embedded"> & {
  onSnapshot: SnapshotDialogProps["onSubmit"]
  onHistory: (input: HousingOccupancyExportInput) => Promise<void>
}) {
  const [kind, setKind] = useState("snapshot")
  return <HousingDialogShell title="导出房态" pending={props.pending} error={null} focusFallbackRef={props.focusFallbackRef} onClose={props.onClose}>
    <label>导出类型<select aria-label="导出类型" value={kind} disabled={props.pending} onChange={(event) => setKind(event.target.value)}><option value="snapshot">当前房态</option><option value="history">入住记录（含历史）</option></select></label>
    {kind === "snapshot" ? <HousingRoomStatusExportDialog {...props} embedded onSubmit={onSnapshot} /> : <HousingOccupancyExportDialog {...props} embedded onSubmit={onHistory} />}
  </HousingDialogShell>
}
