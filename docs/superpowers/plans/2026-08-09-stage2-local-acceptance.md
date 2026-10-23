# Stage 2 Local Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development for bounded tasks, superpowers:systematic-debugging for every failure, and superpowers:verification-before-completion before declaring the stage accepted.

**Goal:** 在非 Docker 本地环境联合验收评价中心六个实施单元，证明系统评价、教学评语、营期问卷、隐私、即时撤权、打印和迁移准备在四种视口及完整 PostgreSQL 门禁下可重复通过。

**Architecture:** 使用一个受保护的临时 PostgreSQL 数据库和独立临时媒体/隔离/Worker 目录，启动 ERP API、ERP Web、score-tool Web 与 Worker；由单一安全 runner 检查环境、建库迁移、seed、readiness、执行跨模块 Playwright、收集媒体清单并无条件清理。验收只产出可复现证据和迁移准备材料，不构建镜像、不接触 NAS 或旧媒体。

**Tech Stack:** Bash、Python/Pytest、Alembic、PostgreSQL、Node/Vitest/Playwright、Ruff、mypy、ESLint、TypeScript、Vite

---

## Preconditions and exclusions

- 依赖教学评语计划与营期评价计划的全部聚焦门禁通过。
- ERP 工作树固定为 `/Users/skymoonzyj/Documents/new project3/.worktrees/system-evaluation-processing`；score-tool 工作树固定为 `/Users/skymoonzyj/Documents/new project3/.worktrees/game-score-system-evaluation-processing`。
- runner 必须拒绝默认/生产/非本机数据库，拒绝复用非空媒体目录，拒绝缺失或互相相同的 v1/Web/Worker token。
- 只写系统临时目录和测试数据库；不得读取、迁移、删除 score-tool 旧 `data/`、`uploads/`、`runtime/` 或任何真实截图/录像。
- Docker、Compose、NAS、GPU、录像分析、天赋测评、支付平台和公安同步继续延期。

## Acceptance matrix

| Journey | Roles | Required evidence |
| --- | --- | --- |
| 系统评价 | 学员/监护人/教练/审核人 | 上传、匹配、处理、审核、发布、修订、隐私 |
| 教学日报 | 主教练/授权人员/审核人/家庭 | 中国日界、补录、驳回整改、发布修订 |
| 教学周报 | 主教练/授权人员/审核人/家庭 | 21 日三周、不足周、截止、版本 |
| 营期问卷 | 学员/多个监护人/工作人员/管理员 | 独立提交、一次修改、匿名反馈、打印 |
| 即时撤权 | 所有受保护角色 | 关系/分配/override 改变后下一请求拒绝并清缓存 |
| 响应式 | 1440/1024/768/360 | 无横向裁切、遮挡、不可达操作或身份泄漏 |

### Task 1: Freeze launcher safety and deterministic acceptance seed

**Files:**

- Create: `scripts/seed-stage2-acceptance-e2e.py`
- Create: `scripts/run-stage2-local-acceptance.sh`
- Create: `apps/api/tests/cli/test_seed_stage2_acceptance_e2e.py`
- Create: `apps/api/tests/cli/test_stage2_acceptance_launcher.py`
- Modify: `apps/api/tests/cli/test_e2e_database_safety.py`
- Modify: `apps/api/tests/cli/test_e2e_launchers.py`

- [ ] RED 测试验证危险数据库、非 loopback host、非空目录、相同 token、缺失依赖、端口占用和 readiness 文本不精确时 runner 在建库/写媒体前失败。
- [ ] Seed 幂等创建所有旅程所需账号、关系、21 日/不足周营期、工作人员历史、游戏账号、分析配置和权限；输出 JSON 仅含测试 UUID、用户名和环境变量名，不含密码/token/手机号/证件。
- [ ] runner 用 trap 保证服务、数据库和临时目录在成功/失败/中断时清理；日志屏蔽 Authorization、Cookie、CSRF、票据、手机号、证件、游戏 ID 与正文。
- [ ] 先观察 RED，再实现并运行 shellcheck 可用子集、`bash -n` 和聚焦 CLI pytest GREEN。

### Task 2: Add one cross-module four-viewport Playwright suite

**Files:**

- Create: `apps/web/tests/stage2-local-acceptance.spec.ts`
- Modify: `apps/web/tests/helpers.ts`
- Modify: `apps/web/playwright.config.ts`

- [ ] RED 旅程串联系统评价已发布结果、同报名日报/周报、家庭已发布视图、结营问卷、工作人员匿名反馈和管理员打印。
- [ ] 每次权限/关系/工作人员分配变化后不刷新 token，直接发下一请求并断言 403 或私有 404；页面必须取消请求、清当前账户缓存、关闭敏感 dialog 并导航到安全落点。
- [ ] 捕获所有 ERP/score-tool JSON 响应，递归拒绝手机号、身份证、内部媒体路径、绝对路径、服务 token、隐藏评价者身份和无关工作人员内容。
- [ ] 四项目固定为 `desktop-1440`、`desktop-1024`、`tablet-768`、`mobile-360`；所有旅程使用语义定位器与真实 API，不使用页面内假数据。

### Task 3: Stabilize pre-existing full-suite timing failures by evidence

**Files:**

- Modify only if a stable reproduction proves a product/test defect: `apps/web/src/evaluations/useEvaluationAccessError.ts`
- Modify only if the assertion itself is the defect: `apps/web/src/evaluations/AnalysisProfilesPanel.test.tsx`
- Create: `apps/web/src/evaluations/useEvaluationAccessError.test.tsx`

- [ ] 记录当前基线：Web 全量曾出现 `AnalysisProfilesPanel` 撤权后两个活动查询重新进入缓存；聚焦用例及该文件 15 项均通过，因此不能先猜修复。
- [ ] 用至少 10 次聚焦文件、至少 3 次全量套件和 TanStack Query fetch/cancel/remove 顺序证据建立单一根因；若不能稳定复现，保留代码并把门禁设为连续全量通过，不添加 sleep 或延长 waitFor。
- [ ] 若稳定复现，先新增最小失败测试，再修复源头；断言必须证明撤权后活动请求无法重新填充 root，且 ERP session 保持有效。
- [ ] 修复后聚焦连续 10 次、全量连续 3 次均通过才关闭该项。

### Task 4: Run complete API, database, migration and static gates

**Files:**

- Modify if required: `scripts/db-schema-manifest.sh`
- Modify: `README.md`
- Create: `docs/operations/stage2-local-acceptance.md`
- Modify: `apps/api/tests/cli/test_operations_docs.py`

- [ ] 从空临时数据库执行 Alembic upgrade head，schema manifest；对 0013→0014→0015→0016 做安全 downgrade refusal 和空库往返。
- [ ] API 全量不得跳过 PostgreSQL 测试；检查 pytest summary 中 skip 原因，数据库/并发类零 skip。
- [ ] 运行 Ruff、mypy、Web Vitest、ESLint、build 和 score-tool 全量；任何失败按系统化调试处理，不批量掩盖。
- [ ] 运维文档记录本地拓扑、秘密隔离、临时目录、精确命令、延期范围、媒体清单和最终 Docker/NAS 迁移前置条件。

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest -p no:cacheprovider apps/api/tests -q
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m ruff check apps/api
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m mypy apps/api/app
npm --prefix apps/web test -- --run
npm --prefix apps/web run lint
npm --prefix apps/web run build
cd '/Users/skymoonzyj/Documents/new project3/.worktrees/game-score-system-evaluation-processing'
python3 -m pytest -q
```

预期所有门禁退出 0；只允许规格已记录且与 PostgreSQL/权限/隐私无关的显式 skip。

### Task 5: Verify media manifest and migration readiness without moving data

**Files:**

- Modify: `scripts/evaluation-media-manifest.py`
- Modify: `apps/api/tests/cli/test_evaluation_media_manifest.py`
- Create: `docs/operations/stage2-migration-readiness.md`
- Modify: `apps/api/tests/cli/test_operations_docs.py`

- [ ] 清单测试覆盖已发布、待审、隔离、孤儿引用、缺文件、大小/hash 不符和符号链接逃逸；输出只含 opaque key、sha256、size、状态与关联 UUID。
- [ ] 对临时验收媒体生成清单并逐项与数据库匹配；不复制文件、不连接 NAS、不声明备份恢复完成。
- [ ] 文档冻结最终迁移顺序：停写→数据库备份→媒体清单→复制→hash 校验→数据库恢复→关系校验→冒烟→保留回滚源；明确此阶段未执行。

### Task 6: Execute official runner and independent final reviews

**Files:**

- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-30-teaching-evaluation-center-design.md`
- Modify: `apps/api/tests/cli/test_operations_docs.py`

- [ ] 使用全新测试数据库和空临时目录运行 `scripts/run-stage2-local-acceptance.sh`，不得复用开发服务。
- [ ] 保存机器可读 summary：迁移版本、API/Web/score-tool 测试数、四视口旅程数、数据库并发测试数、媒体条目数、skip 原因、清理状态和 commit SHA；不得包含个人信息或秘密。
- [ ] 独立规格审查覆盖所有冻结规则，独立质量/安全审查覆盖私有 404、失败零写入、自审、匿名、打印审计、锁顺序和缓存撤权；Important 以上问题修复后复审。
- [ ] 最后执行：

```bash
./scripts/run-stage2-local-acceptance.sh
git diff --check
git status --short
```

完成标准：官方 runner 单次完整通过、连续 Web 稳定性门禁通过、独立审查为 PASS、只保留预期源码/文档改动。此结论仅为“阶段 2 本地验收完成”，不得表述为 Docker、NAS、生产、GPU 或数据迁移完成。
