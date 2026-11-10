import { modeLabels, type RunView } from './api'
const states: Record<string, string> = { prepared: '正在准备，等待连接器确认', ready: '准备完成，等待明确开始', starting: '开始请求已发送，等待执行确认', running: '训练进行中', stopping: '停止请求已发送，尚未确认灯已熄灭', unconfirmed: '停止未确认，需要现场检查', awaiting_sync: '待同步：已确认熄灯，尚未收到服务器保存回执', uncertain_closed: '执行状态未知，需复测', completed: '已保存', cancelled: '已取消，未开始执行', interrupted: '训练中断', expired: '租约已失效' }
export function RunPanel({ run, canOperate, canStop = canOperate, busy, stopping = false, stopPending = false, onStart, onStop, onActivate }: { run: RunView | null; canOperate: boolean; canStop?: boolean; busy: boolean; stopping?: boolean; stopPending?: boolean; onStart: () => void; onStop: () => void; onActivate: (device: string) => void }) {
  if (!run) return null
  return <section className="rx-card" aria-label="训练状态"><div className="rx-section-heading"><span className="rx-step">03</span><h2>训练状态</h2></div><p className="rx-status" role="status">{states[run.status] ?? '状态未知，请刷新确认'}</p><p>{modeLabels[run.manifest.mode]}{run.manifest.mode === 'manual' ? ' · 不参与排名' : ''}</p>
    {run.result_saved && !run.stop_confirmed ? <p role="alert">结果已保存，但停止未确认。保存回执不代表设备已熄灯。</p> : null}
    {!run.result_saved && !['cancelled', 'uncertain_closed'].includes(run.status) ? <p>保存状态：{run.stop_confirmed ? '待同步' : '等待训练结束及服务器回执'}；当前不显示名次。</p> : null}
    {run.status === 'uncertain_closed' ? <><p>后台已归档，不生成成绩、不参与排名，原运行与审计保留。</p><p>尚不能确认原连接器已收到并持久化归档凭据；本地待处理配额是否释放，请回原连接器核对。</p></> : null}
    {stopping && !run.stop_confirmed ? <p>已请求停止；已禁止新刺激和租约续期，仍需等待服务器与设备的熄灯确认。</p> : null}
    {!canOperate && !canStop ? <p>当前仅可读取状态；本标签页没有可用控制权，不会自动恢复或开始。</p> : <div className="rx-actions">{canOperate && !stopping && run.status === 'ready' ? <button className="primaryButton" disabled={busy} onClick={onStart}>开始训练</button> : null}{canStop && !run.stop_confirmed ? <button disabled={stopPending} onClick={onStop}>停止并确认熄灯</button> : null}{canOperate && !stopping && run.status === 'running' && run.manifest.mode === 'manual' ? run.manifest.devices.map((device, index) => <button key={device.device_key} disabled={busy} onClick={() => onActivate(device.device_key)}>点亮第 {index + 1} 位 · 灯 {device.display_number}</button>) : null}</div>}
  </section>
}
