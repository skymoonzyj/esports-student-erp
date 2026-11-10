# 端游 FPS 可选全屏 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在天赋测试 2 的端游 FPS 中提供开始前可选的全屏模式，保持现有测试与数据规则。

**Architecture:** 在现有 AssessmentHost 生命周期中管理自身容器的原生全屏状态；展示尺寸继续固定 16:9。全屏切换和开始按钮分离，事件与失败处理不得触发尝试创建或削弱中断保护。

**Tech Stack:** React 19、TypeScript、原生 Fullscreen API、Vitest/Testing Library、现有浏览器验收环境。

**Spec:** docs/superpowers/specs/2026-11-08-pc-fps-fullscreen-design.md

## Global Constraints

- 只对端游 FPS 显示入口；手游及 MOBA 保持原样。
- 场地维持 16:9 等比放大，非 16:9 屏幕留边，不拉伸、不裁切。
- 不改变玩法、计时、评分、排名、权限或数据库，不自动修改旧轮次。
- 全屏切换不得自动创建、保存或放弃尝试；运行中退出或尺寸变化仍中断，不续接计时。
- 不新增依赖；保留现有未提交改动。使用已有 profile-housing-edit 隔离工作树，不重建、不整库提交或重置。
- NAS 发布仅通过 scripts/deploy-nas.sh，失败阻断交付；不写入生产测试成绩。

## Review Focus

- 全屏请求拒绝或浏览器不支持：窗口模式仍可启动，无重复请求和悬挂禁用状态。
- 快速双击及全屏请求未决时点击开始：不重复申请，不抢先建立服务端尝试。
- 请求未决时卸载或切换账号/项目：无跨账号状态回写，仅清理本容器拥有的全屏。
- Esc、失锁、resize、fullscreenchange 连续到达：一次中断，不提交、不重启计时。
- 小屏/超宽屏、长说明和保存结果：16:9 场地与计时条不被裁切，按钮可达，窗口和非 FPS 页面不回归。

---

### Task 1: 全屏容器、生命周期和回归

**Files:**
- Modify: apps/web/src/esportsAssessments/AssessmentHost.tsx
- Modify: apps/web/src/esportsAssessments/assessment.css
- Test: apps/web/src/esportsAssessments/AssessmentHost.test.tsx
- Create: docs/superpowers/verification/2026-11-08-pc-fps-fullscreen.md

**Interfaces:**
- Consumes: existing AssessmentHostProps, AssessmentSessionController.start/interrupt/submit, sameStageGeometry and document.fullscreenElement/fullscreenchange.
- Produces: unchanged public Host/API contracts; accessible “进入全屏／退出全屏” action and scoped fullscreen container styling. No game-engine or backend changes.

- [x] **Step 1: 建立基线并记录本轮前的文件快照。** 在 apps/web 运行 `npm test -- src/esportsAssessments/AssessmentHost.test.tsx --maxWorkers=2`，确认旧行为通过；保留 dirty worktree，供评审只比较本轮增量。
- [x] **Step 2: 添加失败测试并验证 RED。** 覆盖 only_pc_fps_has_fullscreen_action、request_does_not_start_attempt、denial_keeps_window_mode、pending_blocks_duplicate_and_start、active_phase_blocks_toggle、fullscreen_exit_interrupts_once_without_submit、late_request_after_unmount_is_owner_scoped；同时断言实际环境尺寸来自切换完成后的场地。
- [x] **Step 3: 实现最小全屏生命周期。** 在 Host 内加入受控全屏状态/未决状态、仅本人容器事件处理及异步清理；开始前等待布局稳定，状态不确定时禁止开始。失败使用中文提示，窗口模式及原有权限/恢复/中断分支不变。
- [x] **Step 4: 实现全屏布局。** 在 assessment.css 为 Host 的全屏状态添加主题兼容样式；按可用宽高确定 16:9 场地，计时和退出提示在场地外固定占位。准备内容可滚动；开始测试前归位，不因隐藏准备内容再改变场地尺寸。
- [x] **Step 5: 验证 GREEN。** 运行 `npm test -- src/esportsAssessments --maxWorkers=2`；新旧 Host、场景引擎、输入和保存测试全部通过。对照本轮前快照做独立规格/代码复核，修正发现的问题后复核。
- [x] **Step 6: 全量静态与回归。** 在 apps/web 运行 `npm test -- --maxWorkers=2 && npm run build && npm run lint`，在源码根执行 `git diff --check`，记录准确计数和已有警告。
- [x] **Step 7: 真实浏览器验收。** 仅使用隔离账号/数据，逐项验证网格、六目标、跟枪的全屏试练及正式保存；另外验证正式途中 Esc 中断且无成绩、返回窗口重新开始、非 16:9 留边、返回项目列表与切账号清理。通过真实点击申请全屏/鼠标锁定；缺少前台窗口则明确记录未验收并暂停发布，不用模拟结果代替。
- [x] **Step 8: 完整增量复核及发布。** 收束本轮实际增量，独立最终复核；不自动提交混杂的既有更改。从当前源码执行 `PATH='/Volumes/MacDev/Projects/new project3/.venv/bin':"$PATH" NAS_SSH_PORT=202 scripts/deploy-nas.sh --source "$PWD"`，发布失败立即停止，不绕过脚本。
- [x] **Step 9: 交付核验。** 只读核对 NAS .active-release、服务健康、新静态资源和发布脚本的备份/恢复结果；清理仅本轮隔离环境与临时凭据，保留脱敏验收记录，给用户全屏按钮位置和 Esc 行为说明。
