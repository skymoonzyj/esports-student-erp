import type { AssetHardware } from "./api"
import "./assets.css"

export const hardwareFields = [
  { key: "cpu_model", label: "CPU 型号", max: 120, example: "例如：Intel i7-14700 / Ryzen 7 7800X3D" },
  { key: "memory_capacity", label: "内存容量", max: 80, example: "带单位，例如：32GB" },
  { key: "gpu_model", label: "GPU 型号", max: 120, example: "例如：RTX 4070、核显或无独显" },
  { key: "disk_capacity", label: "硬盘容量", max: 120, example: "带单位，例如：1TB SSD + 2TB HDD" },
] as const

export function isComputer(category?: string | null): boolean {
  return category === "台式电脑" || category === "笔记本电脑"
}

export function normalizedHardware(value: AssetHardware): AssetHardware {
  return Object.fromEntries(hardwareFields.map(({ key }) => [key, value[key]?.trim() || null]))
}

export function hardwareChanged(value: AssetHardware, original: AssetHardware): boolean {
  return hardwareFields.some(({ key }) => (value[key]?.trim() || null) !== (original[key]?.trim() || null))
}

export function hardwareRequired(value: AssetHardware & { category?: string | null }, original?: AssetHardware & { category?: string | null } | null): boolean {
  return isComputer(value.category) && (!original || value.category !== original.category || hardwareChanged(value, original))
}

export function hardwareComplete(value: AssetHardware): boolean {
  return hardwareFields.every(({ key }) => Boolean(value[key]?.trim()))
}

export function AssetHardwareFields({ value, onChange, category, required = true, idPrefix = "asset", disabled = false }: {
  value: AssetHardware
  onChange: (value: AssetHardware) => void
  category?: string | null
  required?: boolean
  idPrefix?: string
  disabled?: boolean
}) {
  if (!isComputer(category)) return null
  const historicalIncomplete = !required && !hardwareComplete(value)
  return <fieldset className="assetWarrantyFields assetHardwareFields" disabled={disabled}>
    <legend>电脑配置{required ? "（必填）" : historicalIncomplete ? "（历史缺项待补充）" : ""}</legend>
    {historicalIncomplete ? <small>可先修改其他信息；如修改配置，需补齐以下四项。</small> : null}
    {hardwareFields.map(({ key, label, max, example }) => <div className="assetField" key={key}>
      <label htmlFor={`${idPrefix}-${key}`}>{label}</label>
      <input id={`${idPrefix}-${key}`} value={value[key] ?? ""} maxLength={max} required={required} placeholder={example} onChange={(event) => onChange({ ...value, [key]: event.target.value })} />
    </div>)}
  </fieldset>
}
