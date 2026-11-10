# 人员档案：身份与项目、教练、队伍分配

用户要求在人员档案内设置角色，并按学员/队员身份分配项目、教练或队伍；已明确选择“按推荐方案开发”，本次直接实施，不再次等待设计批准。

## 范围与边界

- 档案页增加“身份与归属”：学员、队员或兼任。保留已有工作人员等档案；新增身份不删除历史学籍、入住、审批或订单。
- 学员可选择多个现有启用项目及这些项目的合格教练。项目是独立档案归属，不自动报名、收费、建订单、确认付款。教练为所选项目的负责人并集，不声称逐项目唯一教练。
- 队员可在档案页加入队伍、退队或转队，分部来自队伍。复用现有队伍服务、权限、单一当前队伍和历史记录，独立保存，不能绕过转队限制。
- 新业务身份允许增加，已有学员/队员身份不可在此删除，界面明确提示保留历史。项目、负责人可调整或清空为待分配。
- 已审批未激活档案也可分配，不生成或激活账号。旧审批 kind/revisions/signatures 保持不变。
- 旧版非代建工作人员首次增加学员时，仅允许真实资料完整的成年人，建立 active 学籍以避免原本可用的住宿资格因 draft 学籍而下降；未成年、年龄未知或资料不全者明确拒绝，须走既有资料审核流程。已有学员及可信已审批代建档案仍可正常维护。
- “账号权限角色”独立面板，复用当前 RBAC 授权，只有现有有权人员可操作；无账号明确提示激活后维护。业务身份不自动赋予账号角色，激活仍遵守原审批身份的安全规则；页面明确提示新增业务身份与登录权限不同。
- 业务身份/项目/教练写入须实际管理员或超管且有目标 people.update；队伍写入仍须现有队伍管理资格。账号角色仍须现有 permissions.manage，不以人员编辑权替代。
- 事务内锁定并重查权限、人员、启用项目及教练资格；版本冲突不覆盖新数据；操作幂等，审计仅记录安全元数据和原因。
- 不修改生产人员的具体分配以测试本功能。

## 实现合同

`GET /people/{person_id}/assignments` 返回：

```json
{"person_id":"uuid","version":"opaque-token","business_kind":"student","source_intake_kind":"staff","student_project_ids":[],"coach_person_ids":[],"capabilities":{"can_edit":true,"can_manage_account_roles":false,"can_remove_identity":false},"eligible_projects":[{"id":"uuid","name":"项目"}],"eligible_coaches":[{"id":"uuid","full_name":"教练","project_ids":["uuid"]}],"account_id":null,"account_status":"not_activated"}
```

business_kind 可为 null/student/player/both；source_intake_kind 可为空。读权限沿用 people.read 目标范围；无编辑权不返回候选目录。account_id 仅在有权维护账号角色时返回。

`PUT /people/{person_id}/assignments`：expected_version、idempotency_key(UUID)、reason(1–200字)、business_kind、student_project_ids、coach_person_ids。完整目标状态，禁止额外字段。team 和 RBAC 不属于此原子保存，使用既有独立接口。成功返回更新后的 GET 形状；相同操作幂等回放不得重复审计/关系。409 assignments_stale / idempotency_conflict / identity_removal_not_supported 使用中文错误。请求失败保留表单；权限失效立即清理敏感候选。

新增 PersonAssignmentState、PersonProjectAssignment(保留 ended 历史)、PersonAssignmentOperation；学生身份沿用 StudentProfile，队员业务标记独立保存。版本指纹含当前档案版本、学籍、负责人和项目关系，保证旧负责人接口也能使新编辑器失效。

旧负责人写接口不能绕过新项目资格规则：已经使用此归属模块的档案须共用校验或明确指引新接口；尚未使用的历史档案保持兼容。

仅扩充无账号队员候选：已审批、审批事实仍可信且新业务标记为队员。已有账号继续要求 active + team_member 等既有条件。不得宽泛放开所有无账号人员。

## 验收

已审批其他工作人员可增加学员/队员并维护归属；已注册学员也可维护；兼任显示两组入口；拒绝无权限、错误项目教练、停用候选、过期版本及不同载荷复用操作键。审批/入住、账号、订单、付款不得隐式变化。端到端用隔离合成数据；通过本地检查、独立复核后走标准 NAS 发布与只读核验。
