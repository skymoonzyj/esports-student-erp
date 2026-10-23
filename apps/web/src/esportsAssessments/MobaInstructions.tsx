import type { TaskConfig } from './contracts'

interface Guide { purpose: string; action: string; example: string; success: string; scoring: string }

function guideFor(config: TaskConfig): Guide | null {
  const p = config.params
  const mobile = config.suite_id === 'mobile-moba'
  const click = ['pc-moba-v2', 'pc-moba-v3'].includes(config.engine_version) ? '左键或右键单击' : '右键单击'
  const guideColor = ['mobile-moba-v2', 'mobile-moba-v3'].includes(config.engine_version) ? '蓝色' : '黄色'
  switch (p.kind) {
    case 'last_hit': return {
      purpose: '观察血量变化，练习最后一击的出手时机。这是固定血量轨迹的时机模拟，不是完整对线。',
      action: `${click}小兵。出手后有 ${p.attack_windup_ms} 毫秒前摇，移动会取消尚未完成的攻击；攻击间隔 ${p.attack_interval_ms} 毫秒。`,
      example: '看血条逐渐下降，提前留出出手时间。若提示过早或过晚，下一次调整点击时机，不要连续乱点。',
      success: `攻击生效时落在本波 ${p.last_hit_window_ms} 毫秒的低血量窗口内。每波最多成功一次，过早攻击不会改变预设血量。`,
      scoring: `${p.opportunity_count} 波，每波 ${p.window_ms / 1000} 秒；正式分数按成功波数占比计算。过早／过晚次数用于解释结果。`,
    }
    case 'rhythm': return {
      purpose: '练习一次有效攻击与移动到位的配合，不测试整场团战决策。',
      action: mobile
        ? `松开移动摇杆后点攻击，等待 ${p.attack_windup_ms} 毫秒完成出手，再用摇杆走入绿色圈。移动中攻击或前摇结束前移动会取消攻击。`
        : `${click}攻击目标，等待 ${p.attack_windup_ms} 毫秒完成出手，再${click}绿色圈移动。出手完成前移动会取消攻击。`,
      example: '推荐“攻击完成 → 移动到圈内 → 留在圈内等下一轮”。这是一种操作建议，规则不强制先后顺序。',
      success: '同一轮内完成一次有效攻击，而且本轮结束时人在绿色圈内。仅攻击、仅到位，或进圈后又离开，都不能算该轮全部完成。',
      scoring: `${p.opportunity_count} 轮，每轮 ${p.window_ms / 1000} 秒；正式分数按两项都完成的轮数占比计算。`,
    }
    case 'dodge': return {
      purpose: '观察来袭路线并移动避开，练习走位避险；不是单独的纯反应速度测试。',
      action: mobile ? '用左侧摇杆移动蓝色角色，避开橙色来袭技能；本项不需要攻击。' : `${click}地面移动蓝色角色，避开橙色来袭技能；本项不需要攻击。`,
      example: `黄色虚线是敌方路线预警，${p.warning_ms} 毫秒后发射。尝试移出路线，而不是沿着来袭方向跑。`,
      success: '本轮没有被技能碰到即为成功。技能瞄准本轮开始时的位置，连续走位也是有效操作。',
      scoring: `${p.opportunity_count} 轮，每轮 ${p.window_ms / 1000} 秒；正式分数按未受击轮数占比计算。首次移动时间只作参考，不额外加分。`,
    }
    case 'dodge_choice': return {
      purpose: '根据危险区域预警选择安全区，练习识别、移动与持续避险；不是单独的纯反应速度测试。',
      action: mobile ? '用左侧摇杆移动蓝色角色，预警出现后完整进入安全圆；本项不需要攻击。' : `${click}安全圆方向移动蓝色角色，预警出现后完整进入安全圆；本项不需要攻击。`,
      example: `黄色纹理表示即将爆发的危险区域。安全圆出现后尽快进入，并在 ${p.burst_ms} 毫秒爆发期间保持安全。`,
      success: '角色边缘也必须进入安全圆，并在整个爆发期间留在圆内；危险结束后才算本轮成功。',
      scoring: `${p.opportunity_count} 轮，每轮 ${p.window_ms / 1000} 秒；正式分数按未受击轮数占比计算。安全区进入用时只用于解释结果。`,
    }
    case 'skill_aim': return {
      purpose: '练习方向控制与对移动目标的提前瞄准。',
      action: `按住技能按钮并拖动，观察${guideColor}瞄准线，松开后发射。不要直接点目标来代替释放技能。`,
      example: `目标在横向移动，瞄准它将要到达的位置。${guideColor}线是自己的技能方向，不是敌方预警${config.engine_version === 'mobile-moba-v1' ? '（旧版画面会误标为“预警”）' : ''}。`,
      success: '发出的技能实际碰到目标才算命中。每轮只接受第一次有效释放，打空后等待下一轮。',
      scoring: `${p.opportunity_count} 轮，每轮 ${p.window_ms / 1000} 秒；正式分数按命中轮数占比计算。角度误差和用时只用于解释结果。`,
    }
    case 'map': return {
      purpose: '同时照顾主画面和小地图，练习双任务注意分配；不是地图战术判断。',
      action: mobile ? '直接点主画面的目标；右上角小地图出现标记时，直接点它所在的象限。本项不使用攻击按钮或移动摇杆。' : '左键点击主画面目标；右上角小地图出现标记时，左键点击它所在的象限。本项不需要右键移动。',
      example: `点完主目标后仍留意右上角。小地图标记只显示 ${p.event_window_ms} 毫秒，每个事件只接受第一次选择，点错后不能改选。`,
      success: '主目标和小地图都要完成。没有提示时不要乱点地图，否则算误报；漏报也不会获得该次成功。',
      scoring: `主目标 ${p.opportunity_count} 次，小地图 ${p.event_count} 次。正式分数为 100 × √（主目标命中率 × 地图有效响应率）；地图有效次数 = 正确 − 点错 − 无提示误报，最低为 0。只做其中一项，总分为 0。`,
    }
    default: return null
  }
}

export function MobaInstructions({ config }: { config: TaskConfig }) {
  const guide = guideFor(config)
  if (!guide) return null
  return <section className="assessmentMobaInstructions" aria-label="本项测试说明">
    <h3>这项测什么？</h3><p>{guide.purpose}</p>
    <dl><div><dt>怎么操作</dt><dd>{guide.action}</dd></div><div><dt>操作示例</dt><dd>{guide.example}</dd></div>
      <div><dt>怎样才算成功</dt><dd>{guide.success}</dd></div><div><dt>如何计分</dt><dd>{guide.scoring}</dd></div></dl>
    <p className="assessmentMobaGuideNote">先用不保存成绩的试练熟悉操作。正式测试为内部训练参考，不等同于游戏段位或职业水平；自由练习只保存原始指标。</p>
    {config.suite_id === 'pc-moba' && config.test_id !== 'map-awareness' && <p className="assessmentMobaGuideNote">使用右键时请单击；如浏览器启用了右键拖动返回手势，可关闭该手势{['pc-moba-v2', 'pc-moba-v3'].includes(config.engine_version) ? '或改用左键操作' : ''}。</p>}
  </section>
}
