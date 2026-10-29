# V1.0 范围与验收账本

更新：2026-10-29。范围以 [v1-plan.md](v1-plan.md) 为准。用户确认“暂时 mock”：企业 ERP/MES/模型试点使用合成环境；完整 V1 软件范围不缩减。当前 **V1 开发及 mock 范围验收已完成**。三种真实数据库服务均通过合成数据集成验证；SQL Server 使用完整 x86-64 模拟虚拟机，企业试点及生产性能不在本次完成声明内。

## 功能及证据

| ID | 实现及验证证据 | 当前状态 |
|---|---|---|
| CON-01 | Connector SPI/entry point 注册；新 kind 经公开 API→发现→Worker 采集，`test_installed_entrypoint_connector_is_usable_via_public_api` | 已通过 |
| CON-02 | PostgreSQL16/MySQL8.4/SQL Server2022 真实服务：类型/主键、相同水位分页、DELETE 精确权限错误码；MSSQL 高权限账号拒绝及唯一/过滤索引检查 | 已通过（SQL Server 使用 x86-64 全系统模拟） |
| CON-03 | API/控制台表、字段、类型、主键、样例；连接成功/失败审计 | 已通过 |
| CON-04 | CSV/XLSX 真实文件，公式/路径/指纹边界；真实本地 SSH/SFTP 服务、固定 host key、错主机拒绝 | 已通过 |
| CON-05 | 全量删除对账、增量双键游标、边界重放；未完成 staging 不可见；独立 Worker 重启/并发租约/重试耗尽 | 已通过 |
| CON-06 | Chromium 人工录制 API→JSON→独立审批→真实下载→独立缓存；密码不落录制；越界/非登录写请求拒绝 | 已通过 |
| CON-07 | 流水线、运行、checkpoint、心跳、故障告警与恢复；控制台运维 | 已通过 |
| ONT-01 | 类型/必填/枚举/上下限/单位/跨属性约束，制造模板；非法完工数阻止发布 | 已通过 |
| ONT-02 | mock 标注候选；可配置 OpenAI-compatible HTTP 结构化响应，模拟服务集成；虚构字段拒绝、只发元数据 | 已通过（模型使用 mock） |
| ONT-03 | React Flow 拖拽及连线、对象/属性/关系编辑、DSL/指标、版本持久化；浏览器节点可见性修复 | 已通过 |
| ONT-04 | 52 canonical entities + 52 aliases + 62 evidenced relationships；冲突显式审批合并；决议撤销保留历史版本 | 已通过 |
| ONT-05 | enum/unit/数值类型正反例、sum/count/ratio；禁止任意代码/SQL | 已通过 |
| ONT-06 | 重复身份、孤儿、错误类型、歧义/基数、M:N junction 证据、NULL 不产生伪关联 | 已通过 |
| ONT-07 | 乐观草案修订、独立发布、冻结依赖、回滚；失败刷新保留旧快照且禁止可信业务调用 | 已通过 |
| CAP-01 | 7 模板 DSL，存储入出参 Schema、版本、角色、限制、规则版本 | 已通过 |
| CAP-02 | 仅消费已发布本体，必需业务关系/类型/身份契约校验，确定性模板，不存在 SQL 输入入口 | 已通过 |
| CAP-03 | 实际动态 REST 调用及按角色 OpenAPI，和源订单/工单核对 | 已通过 |
| CAP-04 | 官方 SDK v2，新协议2026-07-28/旧 SDK v1/无握手/头部校验；真实独立网关调用一致性 | 已通过 |
| CAP-05 | 自动生成正向/非法输入/角色/副作用测试、预览沙箱；依赖快照变化使旧测试失效 | 已通过 |
| CAP-06 | 版本生成、独立发布、已发布版本回滚；REST/MCP 共用权限；管理员自审批拒绝 | 已通过 |
| CAP-07 | 成功/失败/超时记录、延迟、审计、角色过滤、草案幂等 | 已通过 |
| BASE-01 | 租户隔离、拒绝客户端 tenant spoof、行列投影、来源证据授权、禁止原始入口绕过；私有模式禁用旧能力 | 已通过 |
| BASE-02 | API/Worker/MCP 三进程；私有 Compose + PostgreSQL 持久化，重启后恢复业务结果；非 root Chromium | 已通过 |
| PILOT-01 | mock ERP→MES→本体→7能力→REST/两代MCP；源对账与权限；故障/过期/关系冲突测试 | 已通过 mock 验收；真实企业试点延后 |

## 本次实际运行

- `uv run pytest -q`：**48 passed**；覆盖基础规则、连接器、Worker、真实浏览器/SFTP、本体、能力与权限。FastAPI TestClient 的 httpx 弃用提示不影响测试结果。
- `uv run ruff check backend tests scripts`：通过。
- `npm --prefix web run build`：通过。当前主 bundle 约538KB，存在 Vite 大包提示，尚未做按工作台拆包。
- `npm --prefix gateway test`：**3 passed**，官方新旧客户端及协议错误处理。
- `uv run python scripts/v1_database_integration.py postgresql mysql`：两个真实数据库均通过；使用随机只读账号和合成 ERP 表，写入分别收到 `25006` / `1142`。
- `DOCKER_CONTEXT=colima-ontobridge-x86 uv run python scripts/v1_database_integration.py mssql --startup-timeout 1800`：**通过**；SQL Server 2022 `16.0.4305.5`，写入收到 `229`，拒绝有写权限的 `sa` 来源账号；普通唯一索引分页成功，过滤唯一索引拒绝作为全表同步键。
- `uv run python scripts/v1_mock_acceptance.py` + `node gateway/pilot-check.mjs`：本机独立进程端到端通过。
- `uv run python scripts/v1_container_acceptance.py`：私有模式、独立 PostgreSQL/API/Worker/MCP、发布后重启恢复、非 root Linux Chromium **通过**。API 18787 / MCP 18790。
- `uv run python scripts/v1_browser_acceptance.py`：私有工程师/访客登录、三个工作台、7 个可见图谱节点、会话刷新和 390px 手机宽度通过，零 pageerror。修复了私有模式旧告警字段造成的白屏；本体节点拖拽保存及能力真实调用也已在浏览器核对。截图位于忽略目录 `output/playwright/`。

无凭据的三库回执快照见 [v1-database-receipts.json](v1-database-receipts.json)。验收记录保存在 `var/v1-console/acceptance.json`、`var/container-acceptance/receipt.json`；它们只包含合成结果，不包含令牌。源码、锁文件、测试、构建及验收脚本均在仓库中。凭据位于忽略目录及 0600 文件。

## 验证环境与未覆盖范围

SQL Server 最初在 ARM 主机的容器级翻译环境因地址映射失败。后续在独立 Colima/QEMU **完整 x86-64 虚拟机**启动真实 SQL Server 服务并完成验收；VM 磁盘位于 `/Volumes/ExtSSD/ob-v1-vm`，默认 Docker context 与其他项目保持不变。

这次真实服务验证发现并修复了 SQLAlchemy MSSQL 方言不实现 `get_unique_constraints` 导致的增量读取故障：改用主键与全表唯一索引；同时拒绝过滤唯一索引，避免游标跳过重复记录。SQLite 回归测试与真实 SQL Server 检查均覆盖该边界。

微软仅支持在 Linux Intel/AMD x86-64 上运行 SQL Server 容器，模拟/翻译环境不属于受支持的生产部署，参见[官方部署说明](https://learn.microsoft.com/en-us/sql/linux/sql-server-linux-docker-container-deployment?view=sql-server-ver17)。本次结果证明连接功能，不宣称原生硬件兼容认证或生产性能。`.github/workflows/ci.yml` 已配置三库 x86-64 验收任务，但尚未远程运行。

真实企业网络、账号、业务字段、产能/库存口径和大规模性能尚未验收；用户当前明确选择 mock，未对真实 ERP/MES 进行任何采集。真实 LLM 由管理员配置后另行测评，不把合成模型 HTTP 测试宣称为模型效果验证。

## 架构决定和边界

采用 FastAPI/Pydantic/SQLAlchemy + React/TypeScript，保留已有业务内核。PostgreSQL 租约替代当前规模的 Redis 队列，持久化文件卷替代 MinIO，官方 TS SDK v2 单独提供 MCP；API 合同隔离这些实现选择。

V1 提供只读查询及审批草案，不提供源系统回写、大规模 CDC、企业 SSO 或任意 RPA 脚本。增量对历史回填/物理删除需要全量校准；数据库分批读取不保证跨批事务快照；风险模板的共享计算要求完整授权范围；当前超时预算不是 CPU 线程硬抢占。详见 [运维手册](v1-operations.md)。
