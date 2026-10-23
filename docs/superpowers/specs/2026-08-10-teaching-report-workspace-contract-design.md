# 教学评语作者工作台补充契约设计

- **日期：** 2026-08-10
- **状态：** 本地实现与 Stage 2 本地联合验收已通过
- **所属规格：** `2026-07-30-teaching-evaluation-center-design.md`
- **所属计划：** `2026-08-09-teaching-reports.md` Task 6

## 1. 背景与目标

现有教学评语后端已经实现 12 条冻结路由、日报/周报状态机、作者与审核授权、家庭已发布投影。Task 6
实施时确认了两个契约缺口：

1. 作者端的 `slots` 和 `reports` 都要求调用方预先知道 `enrollment_id`，但系统没有向仅获
   `student_reports.write` 权限的助教、生活老师或其他工作人员提供安全的可写报名目录。
2. 现有详情响应只返回当前 slot 和草稿，不能满足作者版本时间线、驳回原因以及审核页版本比较。

本补充设计解冻评语 API 路径集合，增加两个只读端点，并定义 Task 6 作者工作台、审核页、家庭入口和撤权
处理所依赖的完整数据边界。完成后评语 API 路径由 12 条增加为 14 条。

本补充不改变评语周期、截止时间、状态机、写入幂等、审核规则或数据库表，也不新增数据库迁移。

## 2. 范围

### 2.1 包含

- 当前账号可写报名的授权后目录、完整筛选 facets 和分页。
- 作者或获权审核人可见的不可变提交版本时间线。
- 作者工作台的目标筛选、slot 加载、草稿、提交、整改、修订和版本比较。
- 审核页版本比较与现有审核动作。
- 评价中心入口、作者/审核子能力路由守卫和即时撤权清理。
- 家庭学习页的已发布教学评语卡片。
- 360px 响应式、44px 触控目标、底部安全区、Dialog 滚动锁和错误恢复。
- 后端授权/隐私测试、前端单元测试、ESLint、严格 TypeScript 和 production build。

### 2.2 不包含

- 新的评分维度、可视化分数或自动生成评语。
- 家庭读取历史未发布版本、驳回原因或内部审核信息。
- 付款、报名、公安报送、截图、录像、GPU、NAS 或 Docker 改动。
- Stage 2 四视口 Playwright 旅程不属于本补充契约的实现范围；现已由独立联合验收入口完成。

## 3. 方案选择

### 3.1 采用：独立的目标目录和版本时间线端点

新增：

```text
GET /api/v1/evaluations/reports/targets
GET /api/v1/evaluations/reports/{report_id}/versions
```

目标目录只负责投影当前作者真正可访问的报名；选择目标后继续使用现有 `slots` 和 `reports`。版本端点只负责
不可变提交版本和相应审核结果，不复制当前草稿或写动作。

这一方案保持单一职责，支持分页和即时撤权，也不会扩大通用报名读取权限。

### 3.2 不采用：把目标塞进 `/reports/context`

Context 应保持轻量能力查询。把全部报名放进 context 会让路由守卫读取不可分页的大响应，并把能力刷新与目录
缓存耦合。

### 3.3 不采用：复用通用 `/enrollments`

通用报名目录依赖 `enrollments.read`，并可能投影付款等作者工作台不需要的信息。给仅获评语填写权限的助教或
生活老师追加该权限会扩大业务可见面，不符合最小权限原则。

## 4. 作者目标目录 API

### 4.1 请求

```text
GET /api/v1/evaluations/reports/targets
    ?cohort_id=
    &project_id=
    &class_id=
    &student_id=
    &query=
    &page=1
    &page_size=20
```

规则：

- `cohort_id`、`project_id`、`class_id`、`student_id` 为可选 UUID。
- `query` 去首尾空白后为空时视为未提供，否则长度为 1–80，只匹配学员姓名和学号。
- 模糊搜索必须转义 `%`、`_` 和转义符本身，不允许通配符扩大查询。
- `page >= 1`，`1 <= page_size <= 100`。
- 未授权、随机或层级不一致的筛选 ID 不返回对象存在性错误，只得到 `200` 空页。

### 4.2 响应

响应为严格闭合模型：

```json
{
  "items": [
    {
      "enrollment_id": "uuid",
      "student_person_id": "uuid",
      "student_name": "学员姓名",
      "student_number": "学号",
      "cohort_id": "uuid",
      "cohort_name": "营期名称",
      "cohort_starts_at": "2026-08-01T01:00:00Z",
      "cohort_ends_at": "2026-08-21T10:00:00Z",
      "project_id": "uuid",
      "project_name": "项目名称",
      "class_id": "uuid",
      "class_name": "班级名称"
    }
  ],
  "facets": {
    "cohorts": [
      {
        "id": "uuid",
        "name": "营期名称",
        "starts_at": "2026-08-01T01:00:00Z",
        "ends_at": "2026-08-21T10:00:00Z"
      }
    ],
    "projects": [
      {
        "id": "uuid",
        "name": "项目名称",
        "cohort_ids": ["uuid"]
      }
    ],
    "classes": [
      {
        "id": "uuid",
        "name": "班级名称",
        "cohort_id": "uuid",
        "project_id": "uuid"
      }
    ]
  },
  "total": 1,
  "page": 1,
  "page_size": 20
}
```

`facets` 从当前账号全部授权目标计算，不受本次请求筛选和分页截断影响。Web 根据项目的 `cohort_ids` 以及
班级的 `cohort_id`、`project_id` 在本地联动项目和班级选项；学员使用 `query` 搜索和结果列表，不下发全量
学员 facet。

目录不得返回手机号、邮箱、身份证、监护关系、性别、出生日期、付款、游戏 ID、AdmissionCase、作者授权
来源、assignment ID、permission ID 或内部审计信息。

### 4.3 授权与目录成员资格

一次请求固定同一个有时区 `occurred_at`。授权顺序为：

1. 要求标准账号和 `evaluation_center.access`。
2. 独立判断账号是否具备任何 `student_reports.write` 作者能力；完全没有能力返回
   `403 evaluation_operation_denied`。
3. 构造授权目标关系，再执行筛选、总数、排序、facets 和分页。

一个报名只有在同一账号、同一 `occurred_at` 调用现有 `author_workspace(enrollment_id)` 能返回至少一个
授权 slot 时才属于目录。该等价关系同时覆盖：

- 当前或历史有效的主教练默认作者资格。
- 在真实项目/班级服务范围内、显式获得评语填写权限的助教、生活老师和其他工作人员。
- 与评语周期实际重叠的服务关系和已有历史评语。
- 正式且已确认的 active/completed 报名。
- active/completed 营期、启用班级和项目、active 学员档案及未删除人员。
- 拒绝优先的全局、营期、项目、班级、报名或学员范围 override。

未来才开始的服务、完全不重叠的服务、已撤销权限、错误范围、取消报名、停用账号、停用学员、删除人员、
停用项目或班级不得出现在 items、facets 或 total 中。目录成员资格不得仅根据通用 RBAC allow 推断。

### 4.4 排序和副作用

稳定排序依次为营期开营时间倒序、营期 ID、项目排序/名称、项目 ID、班级名称、班级 ID、学员姓名、学号、
人员 ID、报名 ID。所有 total 和 facets 均在授权过滤后计算。

该 GET 请求不创建业务、幂等或审计记录，不更新 `last_seen` 等状态；失败路径同样零写入。

## 5. 版本时间线 API

### 5.1 请求

```text
GET /api/v1/evaluations/reports/{report_id}/versions?page=1&page_size=20
```

`page >= 1`，`1 <= page_size <= 100`。结果按 `version` 倒序、版本 ID 稳定排序。

### 5.2 响应

```json
{
  "items": [
    {
      "report_id": "uuid",
      "version_id": "uuid",
      "version": 2,
      "source": "revision",
      "body": "完整评语正文",
      "dimension_values": {},
      "submitted_at": "2026-08-22T02:00:00Z",
      "is_current": true,
      "is_published": false,
      "review": {
        "decision": "rejected",
        "reason": "需要补充训练结论",
        "reviewed_at": "2026-08-22T04:00:00Z"
      }
    }
  ],
  "total": 2,
  "page": 1,
  "page_size": 20
}
```

`review` 在该版本尚未审核时为 `null`。批准结果的 `reason` 必须为 `null`；驳回结果向原作者和获权审核人
返回完整原因。`is_current` 和 `is_published` 分别从报告当前版本指针和已发布版本指针派生。

时间线只包含不可变 `StudentReportVersion`，不包含当前可变草稿。首版继续严格要求
`dimension_values == {}`。

响应不得包含作者或审核人的账号 ID、人员 ID、手机号、证件、审核人姓名、previous version 内部指针、窗口
授权、重开理由、审计 request ID 或幂等记录。

### 5.3 授权

能力和目标授权分开判断：

1. 账号同时没有作者能力和审核能力时返回 `403 evaluation_operation_denied`。
2. 具备作者能力时，可通过现有作者私有选择器读取本人且仍在授权历史范围内的报告。
3. 具备审核能力时，可通过现有审核私有选择器读取当前范围内的报告。
4. 两条选择器任一通过即可返回；两条均不通过时，缺失、错误范围、已删除、已停用、已取消、deny 和随机
   UUID 返回完全相同的私有 404。

家庭、学员或普通工作人员不会因为知道 `report_id` 而获得历史版本。关系终止或权限撤回对下一请求立即生效。
GET 及全部失败路径零业务、幂等和审计写入。

## 6. Web 页面与数据流

### 6.1 评价中心和路由

- `reports_available` 的 TypeScript 类型从字面量 `false` 修正为 `boolean`。
- 评价中心在 `reports_available=true` 时显示可用的“教学评语”入口，链接
  `/evaluations/reports`；为 false 时保留禁用卡片。
- 新建 `TeachingReportCapabilityRoute`，以 `/reports/context` 的实时结果保护子路由，不能仅使用三类能力并集
  `reports_available`。
- `/evaluations/reports`：作者可用时显示作者工作台；只有审核能力时跳转
  `/evaluations/reports/review`；只有家庭能力时跳转现有 `/learning`。
- `/evaluations/reports/review`：必须满足 `reviewer_available`。

报告 context 使用当前账号 query root、`staleTime: 0`、`gcTime: 0`，进入和窗口重新聚焦时先重验。页面私有查询
等待 guard 完成，并绑定授权 revision，避免旧权限数据在重验期间短暂显示。

### 6.2 作者工作台

`TeachingReportsPage` 负责目标筛选和 workspace 组合；具体编辑 Dialog 与版本时间线拆成职责单一的组件：

- 目标目录查询返回完整 facets 和当前页报名。
- 选择报名后调用现有 `slots`，按日报/周报、周期和状态展示任务卡。
- 只有后端 `allowed_actions` 中存在的动作才显示并启用；前端不得根据日期或 status 自行恢复按钮。
- 无 `report_id` 的 slot 在第一次保存时用 `crypto.randomUUID()` 生成报告 ID。
- 同一次结果不确定的请求在重试时复用报告 ID 和幂等键；成功、明确 Problem Details 失败或用户明确放弃后才清除。
- 草稿正文、驳回原因和重开原因只存在于受控表单、一次性 opaque write payload 和 HTTP body，不进入 URL、
  query key、localStorage、sessionStorage 或可检查的长期 mutation variables；mutation 使用 `gcTime: 0`。
- 保存或状态变化成功后失效当前目标 slots、作者列表、详情和版本查询，不把正文手工复制到多个缓存投影。

版本时间线按版本号显示来源、提交时间、审核结果、正文和当前/已发布标记。桌面端前后版本并排，手机端上下
排列；首版不引入文本 diff 依赖，以两个完整、明确标注的版本面板完成比较。

### 6.3 审核页

审核队列继续按营期、项目、班级筛选。选择条目后加载版本时间线，把当前待审版本与上一版本并排/上下比较。
审核按钮仍以服务端审核队列成员资格为依据；作者自审由后端返回类型化 409。

通过或驳回后按当前账号审核队列前缀失效全部筛选组合，并失效该报告详情和版本。驳回原因使用一次性 opaque
payload，不进入 mutation variables 或浏览器存储。

### 6.4 家庭端

现有 `LearningPage` 继续复用多学员切换，并嵌入 `PublishedReportsPanel`。面板只调用
`/students/{student_id}/published-reports`，只展示 `published_version_id` 指向的版本，不调用目标目录或版本
时间线端点。

## 7. 取消、撤权和错误恢复

- 所有 report GET 函数接受 `AbortSignal`，TanStack Query 的 `queryFn` 必须传入 signal，确保路由撤权时
  `cancelQueries` 能中止网络请求。
- 子能力从 true 变为 false 时，先 cancel 当前账号 `reportKeys.root(accountId)`，立即 remove，并在取消完成后
  再 remove，随后返回 `/evaluations`。不得清除其他账号 root。
- 整个 `evaluation_center.access` 被撤回时沿用全局评价错误处理，刷新账号并返回 `/`，不能跳回已无权进入的
  `/evaluations`。
- 已加载目标随后变为私有 404 时，只移除该目标、关闭相关 Dialog 并刷新目录；单个目标 404 不能推断整个
  report 能力消失。
- 403 `evaluation_operation_denied` 触发 report context 重验；只有 context 确认相应子能力为 false 才清 root
  和离开页面。
- 409 并发冲突保留当前表单输入，刷新权威 slot 和版本，提示用户比较后重新确认。
- 网络结果不确定时保留输入、报告 ID 和幂等键；用户重试同一动作时复用它们。
- 目录无匹配项使用正常空状态，不显示权限错误。
- API 格式不符合 Zod 白名单时显示通用刷新提示，不回显原始响应内容。

## 8. 响应式与无障碍

- `teaching-reports.css` 使用独立 `.teachingReport*` 和 `.publishedReports*` 前缀，不复用旧
  `.foundationDialog`。
- `max-width: 768px` 进入单列；360px 不出现页面或内部横向滚动。
- 普通移动页面底部 padding 包含 AppShell 导航高度和 `env(safe-area-inset-bottom)`。
- button、select、input、textarea 以及 radio/checkbox 的可点击 label 至少 44×44 CSS 像素。
- 所有 Dialog 复用 `EvaluationDialogShell`；补充引用计数式 `body` 滚动锁，两个 Dialog/锁并存时关闭一个
  不能提前解锁。
- 保持初始焦点、Tab 环、Escape、pending 禁止关闭、触发器消失后的稳定 fallback，以及全局
  `:focus-visible` outline。
- 加载状态使用 `aria-live="polite"`，错误使用 `role="alert"`，成功使用 `role="status"`。

## 9. 测试设计

### 9.1 后端

服务与 API 测试必须覆盖：

- 主教练默认目录成员；显式获权助教、生活老师和其他工作人员的正确项目/班级范围。
- 未来才开始、完全不重叠、服务结束后无任何历史 slot、撤权、deny、停用账号、删除人员、停用学员、取消
  报名、停用项目/班级以及错误层级全部不可枚举。
- 授权后 facets、total、筛选、分页、稳定排序和通配符转义。
- 完全无作者能力的 403；随机/越权筛选的 200 空页；目录响应没有敏感或内部字段。
- 原作者和获权审核人的版本访问；家庭、其他作者、错误范围、撤权、deny 和随机 UUID 的 403/私有 404
  分流。
- 版本倒序、current/published 指针、pending review、批准、驳回原因以及修订待审期间旧发布版本标记。
- 版本响应不含审核人身份、重开理由、审计和幂等字段。
- 所有目录/版本失败分支前后业务、幂等和审计表计数及既有报告状态完全不变。
- OpenAPI 精确 14 条路由、静态路径顺序、严格 request/response/problem schema 和分页边界。

PostgreSQL 测试必须使用当前工作树 `apps/api` 的 `PYTHONPATH`、隔离的 `TEST_DATABASE_URL`、
`-p no:cacheprovider`，并串行运行聚焦节点。

### 9.2 前端

Vitest 必须覆盖：

- targets/versions URL 序列化、Zod 严格白名单、AbortSignal、账号隔离 query key。
- 目标 facets 联动、姓名/学号搜索、切换报名、日报/周报和状态筛选。
- 新报告稳定 UUID、草稿保存、首次提交、驳回整改、发布修订及网络重试幂等键复用。
- 每个按钮都由 `allowed_actions` 控制；未知 action 或畸形响应被拒绝。
- 版本时间线、作者驳回原因、审核版本比较和作者自审 409 恢复。
- 家庭多学员只读已发布评语，不显示待审、驳回或内部原因。
- 撤权时取消请求、只清当前账号 root、延迟响应不能复活缓存、全局/子能力跳转目标不同。
- 正文和原因不在 query key、浏览器存储或 mutation variables。
- Dialog 焦点、Escape、pending、稳定 fallback 和引用计数滚动锁。
- CSS 具备 360px 单列、44px 触控区域和 safe-area 规则。

Task 6 完成门禁：

```bash
npm --prefix apps/web test -- --run
npm --prefix apps/web run lint
npm --prefix apps/web run build
```

同时运行新增后端聚焦测试、Ruff 和相关模块严格 mypy。全部通过后依次完成规格审查和代码质量审查，修复
Critical/Important 问题并复审。

四视口 Playwright 已由 Stage 2 联合验收入口完成；本补充规格和 Stage 2 本地通过仍不等于 Docker、NAS 或生产部署就绪。

## 10. 兼容性与实施顺序

两个新增 GET 端点为向后兼容扩展。现有 12 条路由的请求和响应保持不变；OpenAPI 精确路径测试从 12 条更新
为 14 条。静态 `/reports/targets` 必须注册在动态 `/reports/{report_id}` 之前，避免 `targets` 被 UUID 动态
路径抢先匹配并错误返回 422。实施顺序为：

1. 后端 targets/versions 服务投影、API 和隐私测试。
2. Web API、query keys、AbortSignal 和路由 guard。
3. 作者工作台和版本组件。
4. 审核页版本比较、家庭面板、评价中心入口。
5. 响应式、Dialog 滚动锁、全量门禁和双阶段审查。

当前继续本地开发，不修改 Docker、NAS、GPU 或录像相关文件。
