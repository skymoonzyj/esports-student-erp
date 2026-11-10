import type { HousingQuickFilter } from "./api"

const OPTIONS: ReadonlyArray<readonly [HousingQuickFilter, string]> = [
  ["occupied", "已入住人员所在房间"], ["vacant", "空房"],
  ["assignable", "可入住"], ["full", "满房"],
  ["problem", "问题、维修或停用房间"], ["pending_verification", "待核对"],
  ["pending_cleaning", "退房待保洁"], ["usable", "有效住宿床位房间"],
  ["cleaning_daily", "每日保洁"], ["cleaning_weekly", "每周保洁"], ["cleaning_on_demand", "按需保洁"],
  ["remaining", "有剩余床位的有效住宿房间"],
  ["single", "有效单人间"], ["double", "有效双人间"], ["triple", "有效三人间"],
  ["custom", "有效自定义房型"], ["functional", "功能间"],
]

export function HousingQuickFilterSelect({ label, value, pending, onChange }: {
  label: string
  value: HousingQuickFilter | undefined
  pending: boolean
  onChange: (value: HousingQuickFilter | undefined) => void
}) {
  return <>
    <label>{label}<select aria-label={label} value={value ?? ""} disabled={pending} onChange={(event) => onChange((event.currentTarget.value || undefined) as HousingQuickFilter | undefined)}>
      <option value="">全部（清除快捷筛选）</option>
      {OPTIONS.map(([key, text]) => <option key={key} value={key}>{text}</option>)}
    </select></label>
    <p className="housingDialog__hint">快捷筛选与其他条件取交集；如需扩大范围，请选择“全部（清除快捷筛选）”。</p>
  </>
}
