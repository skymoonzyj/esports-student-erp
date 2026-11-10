# 委托管理权限与战队工作人员

日期：2026-11-06（中国时间）。用户已明确修订权限边界，并要求“其他按推荐实施”，本次直接开发、测试、发布及对指定账号授权，不再次设置规格/计划等待。

## 已确认目标

- 指定账号获得非财务业务管理能力，同时允许采购报销、订票账单相关权限。支付、退款、支付导入、全部分成权限继续禁止；不授予超级管理员/财务角色，不开放系统备份、审计等超管专属入口。
- 可重置普通人员密码，但不能重置财务或超级管理员人员密码；可以给他人授权，绝不能超过自己的实际有效权限、范围与有效期，不能靠另一个账号回授越权。
- 人员工作人员类型补齐战队经理、教练、助教；主分类与账号兼任角色、队内岗位分开。
- 战队经理、教练、助教可分配到一个或多个队伍，只查看所负责队伍的人员和住宿摘要。新增只读绑定不附带名单编辑、入住、调房或退房权。旧管理绑定的既有功能保留。

## 采用方案与备选

沿用现有 RBAC 和账号独立授权，为指定账号保留已有业务身份、叠加 admin/administration 所需管理角色和明确的金融权限拒绝，不创建与人名绑定的系统角色，不直接提升超管。相比修改所有管理员默认权限，此方案影响范围小；相比超管加菜单隐藏，它在后端真实限制访问。具体授权先预览，再通过正常已登录超管流程写入并复读核验。

队伍工作人员使用新的 TeamStaffAssignment 只读关系，旧 TeamManagerAssignment 不改语义、不复制历史。相比给旧管理绑定加岗位字段，独立关系更容易确保新岗位不意外获得名单管理及协助建档权限。经理复用 team_manager 账号角色，教练复用 coach，新增 assistant_coach 助教角色。

## 委托账号安全边界

1. 禁止的权限集合：payments.read、payments.manage、refunds.manage、payment_imports.manage、全部现有 revenue_share.*；采购 procurement.* 和订票 travel_reports.* 明确允许。已有角色可能隐含支付权限，因此账号级全局 deny 优先，且普通授权人不能删除自己无权恢复的 deny。
2. 保留实际超管门槛，不通过扩大门槛解锁监控、备份/密钥、邮件恢复配置、安全审计、外观、历史住宿作废、ReactionX 等超管功能。招生报名可保留已有付款状态，但无 payments.read 时金额仍脱敏；不把普通学员本人的订单查看等同财务人员。
3. 为所有非超管账号管理者增加共享的受保护目标判断：目标有实际 super_admin/finance 角色，或有非个人范围的核心财务管理/读取允许规则时，不能修改其凭据、启停/删除账号、角色或账号授权。保护检查覆盖变更前状态，防止先移除财务角色/允许项或加 deny 后再重置。
4. 核心财务保护键为 payments.read/manage、refunds.manage、payment_imports.manage、revenue_share.finance/manage/export。self/family 范围、个人分成/邀请权益、采购报销、订票账单不单独将普通人员判为财务人员。采取保守规则：尚未过期（包括未来生效）的结构性财务允许仍受保护，不因临时 deny 解除保护；因此带管理员角色但已被禁止金融权限的账号，也不能由其他非超管管理者篡改，只能超管处理。这是防绕过的实施默认。
5. 每次 mutation 都按人员→账号的既有固定顺序锁定操作者和目标，再重读角色/权限/目标保护状态；统一覆盖密码重置、角色授予/撤销、换岗、覆盖规则增加/删除。不得依赖旧页面或已缓存角色。超管仍可正常管理，既有最后超管、自锁、本人改密等保护保持。
6. 保留既有授权上限和敏感评测有效期/范围规则。管理其他账号不允许赋予自己不具备的角色包、权限、范围或更长授权时间。账号权限变更即时刷新登录能力，审计不记录临时密码或私人资料。
7. 线上账号按姓名精确匹配并核对唯一候选；重名时停止该账号写入并请求补充识别信息，开发/发布可继续。不得把指定账号、真实手机号或人员 ID 写进迁移/种子/提交文件。

## 工作人员分类与队内岗位

- StaffType 新增 team_manager（战队经理）、assistant_coach（助教），保留 coach（教练）及现有分类；一个 StaffProfile 仍为主分类，不强制覆盖账号兼任角色。
- assistant_coach 新内置角色 ID 为 10000000-0000-4000-8000-000000000015；只默认授予 team_memberships.read/assigned_team、personnel_intake.housing.read/global（实际范围由有效岗位绑定限制），不授予建档提交、财务、分成、全局人员/房间或名单写能力。coach 增加 team_memberships.read/assigned_team，仍需真实队内绑定。
- 账号岗位选择支持 team_manager 与 assistant_coach；仍必须明确选择“是否保留其他兼任岗位”、预览并确认，不删除学员/队员/监护身份或原业务历史。
- 新队内岗位值 manager/coach/assistant_coach，分别要求账号角色 team_manager/coach/assistant_coach。绑定按账号实际角色判断，不按单一 StaffProfile 分类判断。新增人员主分类不会隐式开通账号或角色。
- 新 TeamStaffAssignment：id、account_id、team_id、staff_role、created_at、created_by_user_id、ended_at、ended_by_user_id、end_kind；当前唯一(account_id,team_id,staff_role)。支持同人同队兼任与多队兼职；单条解绑不影响其他岗位。
- 绑定/解绑要求现有实际超管+team_manager_assignments.manage，不扩大为所有能管理人员的账号。旧经理管理绑定独立展示，继续原功能，新 UI 清楚显示“只读队内岗位”与“旧名单管理绑定”。
- 保留旧 API；新增 staff-candidates、staff-assignments 创建/列表/结束接口。重复操作沿用 TeamOperation UUID 幂等机制：相同键/载荷重放，变化冲突，重放前复验权限。
- 候选 active、未强制改密、人员未删除、匹配岗位、启用分部/队伍，搜索最少1非空字符，每页最多20，沿用姓名/昵称搜索。失败不泄露其他队伍或敏感身份联系方式。

## 只读队伍范围与生命周期

- 队伍名单 read = 有效旧管理绑定或有效新岗位绑定，且有读取能力并无适用 deny；manage 只认原管理绑定。新经理即使有角色级 manage 规则也不能靠新只读绑定写名单。
- 新岗位归属不能并入用于协助建档写权限的 managed_team_ids；使用独立 readable_staff_team_ids。只接入“我的学员/队员”和授权人员住房摘要；不授予全局人员列表、室友详情或住房写接口。
- 名单、搜索、统计、分页均先按当前角色、岗位、成员关系、启用队伍/分部及 deny 限定。多岗位/多归属去重；跨路径 deny 不可被另一条教练路径救回。
- 角色移除只结束相应新岗位绑定；去掉 team_manager 还沿用结束旧管理绑定。账号删除结束全部绑定；临时停用立即不可读但保留历史。重新授予岗位不恢复已结束绑定。
- 删除/停用队伍或取消分部前，新岗位绑定也作为在用关系检查。调队、入队/离队、原报名/班级教练规则不改；助教不能因此成为班级主教练。
- 读写和生命周期统一现有人员/账号→分部→队伍→关系锁序，换岗先收集所有受影响队伍再统一加锁，不能逐岗位交错加锁。

## API/UI 合同

以现有 /api/v1/team-management 为前缀：
- GET /teams/{team_id}/staff-candidates?staff_role=manager|coach|assistant_coach&query=...&cursor=...&page_size=20 → 现有 ManagerCandidatePage。
- GET /teams/{team_id}/staff-assignments?page=1&page_size=20&status=current|ended → TeamPage[TeamStaffAssignmentView]。
- POST /teams/{team_id}/staff-assignments {account_id,staff_role,idempotency_key} → TeamMutationResult.assignment_id。
- POST /staff-assignments/{assignment_id}/end {idempotency_key} → TeamMutationResult.assignment_id。
- TeamStaffAssignmentView 沿用 id/account_id/full_name/nickname/account_status/started_on/ended_on，增加 staff_role；列表只包含新表，旧绑定由旧面板显示，不混淆可写来源。
- 新 TeamStaffPanel 在队伍页与旧管理面板并列，由 can_assign_managers 控制操作；人员类型筛选、表单、档案、岗位切换、中文角色标签一致，手机适配沿用 A/B 主题。
- 普通工作人员通过“我的学员/队员”查看本队人员住宿；账号/权限/绑定变更时隐藏并清除失效数据，不保留其他身份缓存。

## 迁移与验证

0084_team_staff_roles（down0083）：新增工作人员分类、助教角色及最窄默认读授权；不改已有人员和分配。0085_team_staff_assignments（down0084）：新增只读岗位表/索引，扩展 TeamOperation 允许的岗位绑定/结束类型。有新人员类型、角色指派/自定义权限或岗位历史时降级拒绝，不能静默丢数据；保护检查持有必要表锁。

隔离合成数据验证普通/财务/超管重置与授权上限、降级后重置绕过、锁等待后的撤权、采购/订票允许而支付/分成拒绝；三个岗位分类、兼任/多队去重、只读不变写、解绑/删号/角色撤销、旧管理兼容、迁移保留/降级拒绝、桌面/手机/两主题。部署走 scripts/deploy-nas.sh（SSH202），完成迁移前备份、健康与加密恢复核验；发布后只用正常超管流程对指定账号授权并复读，不以真实重置密码测试。
