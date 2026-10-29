# OntoBridge

企业 AI 连接、本体映射与业务能力平台。将只读 ERP / MES / WMS 数据转为带来源证据、版本和权限的 REST API / MCP 工具。

**当前为 V1 验收候选实现，使用用户确认的 mock 企业数据。** 三个工作台、独立采集 Worker、动态 MCP 网关均已实现；SQL Server 真机验收仍待 Linux x86-64 环境完成，尚不标记完整 V1 验收通过。逐项证据与限制见 [验收账本](docs/v1-status.md)，需求见 [V1 计划](docs/v1-plan.md)。

## 本机运行与 mock 验收

需要 Python 3.11+、uv、Node.js 22。所有命令在仓库根目录执行。

```bash
cd /Volumes/ExtSSD/Github/ontobridge
uv sync --locked
uv run playwright install chromium
npm --prefix web ci
npm --prefix gateway ci
npm --prefix web run build
```

在三个终端中分别运行（使用同一个 `ONTOBRIDGE_DATA`）：

```bash
ONTOBRIDGE_DATA=var/v1-console uv run ontobridge --port 8788
ONTOBRIDGE_DATA=var/v1-console uv run ontobridge-worker
ONTOBRIDGE_API_URL=http://127.0.0.1:8788 ONTOBRIDGE_MCP_PORT=8791 node gateway/server.mjs
```

访问 [控制台](http://127.0.0.1:8788)。默认演示只接受本机请求，左下角切换工程师、审批人、访客。业务数据均为合成样例；模型默认 `mock`，界面和候选明确标注。

```bash
uv run python scripts/v1_mock_acceptance.py --url http://127.0.0.1:8788
node gateway/pilot-check.mjs
```

脚本经公开 API 完成采集、候选生成、独立审批、质量校验、本体发布、能力生成、沙箱测试和能力发布。标准结果：52 个实体、52 个来源身份、62 条有证据关系、7 个发布能力；订单 `SO-2026-0088` 为 1,200 件、完工 864 件、2 张工单。重跑创建新的本体/能力版本，保留历史。

## 三个工作台

| 工作台 | 已实现功能 |
| --- | --- |
| 连接器设计器 | 插件 SDK；PostgreSQL/MySQL/SQL Server/SQLite；CSV/XLSX/SFTP；受限网页录制与审批导出；结构发现；全量/增量流水线；持久化 checkpoint、租约、重试、告警 |
| 本体映射工作台 | 可编辑对象/属性/关系画布；制造模板；可配置模型候选；枚举、单位、数值和跨字段约束；指标；身份合并与撤销；来源别名及关系证据；质量校验；独立审批、版本、回滚 |
| 业务能力工厂 | 已发布本体依赖；7 种批准模板；入出参 JSON Schema；动态 REST/OpenAPI/MCP；自动测试和无副作用沙箱；角色/行列权限；独立发布、版本回滚；调用审计；排产草案审批 |

七种模板：`entity_lookup`、`order_360`、`delivery_risk`、`material_shortage`、`production_progress`、`list_due_orders`、`schedule_draft`。可以给生成的能力自定义名称、说明、允许角色、结果上限、超时及绑定的本体版本。模板和转换均为确定性代码，不接收任意 SQL/脚本。模型只能提出候选，不能审批或发布。

私有模式仅使用 V1 接口；旧 MVP 页面和旧静态能力仅供演示兼容。新客户端使用独立 Node MCP 网关的 `/mcp`，支持官方 2026-07-28 协议和旧版客户端握手。Python stdio 服务属于旧演示接口。

## 私有部署

```bash
python3 scripts/create_local_env.py  # 仅首次；拒绝覆盖已有凭据
mkdir -p config imports/factory
docker compose up -d --build
```

`var/access.json` 保存角色登录令牌（0600），`.env` 保存服务端凭据。控制台使用令牌登录；不会暴露演示登录。来源文件放在 `imports/<tenant>/`，连接配置放在 `config/connections.json`，容器 UID 10001 必须可读且文件权限为 0600。详细配置、权限、备份恢复、模型、RPA 与插件指南见 [V1 运维手册](docs/v1-operations.md)。

Compose 启动 PostgreSQL 元数据库、API/控制台、独立 Worker 和独立 MCP 网关；API 默认绑定 `127.0.0.1:8787`，MCP 为 `127.0.0.1:8790`。企业远程访问由受控 TLS 反向代理提供，按域名配置网关允许主机。源系统始终只读，动作审批后仍然只是草案。

可复现的隔离容器验收（合成来源，独立项目、独立卷与随机凭据）：

```bash
docker compose build console mcp
uv run python scripts/v1_container_acceptance.py
uv run python scripts/v1_browser_acceptance.py
```

它使用 18787/18790 端口，核对 PostgreSQL 持久化、Worker、两代 MCP、重启恢复和非 root Chromium。完成后保留容器供检查；管理命令见运维手册。

## 验证

```bash
uv run ruff check backend tests scripts
uv run pytest -q
npm --prefix web run build
npm --prefix gateway test
uv run python scripts/v1_database_integration.py postgresql mysql
# 在 Linux x86-64 Docker 主机上执行：
uv run python scripts/v1_database_integration.py mssql
```

数据库脚本只创建 `ontobridge-v1-*` 隔离合成服务，不接入企业数据库。SQL Server 的连接实现与 x86-64 CI 已提供，但本机 ARM 容器启动失败，不能把未运行的数据库测试算作通过。

当前版本采用 FastAPI/Pydantic/SQLAlchemy + React/TypeScript，PostgreSQL 或本机 SQLite 持久化，官方 TypeScript MCP SDK v2。附件推荐的框架、Redis/MinIO 被同等功能的持久化租约和文件存储替代，尚不支持大规模 CDC、多节点协调或生产系统回写。
