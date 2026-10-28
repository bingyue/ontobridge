# OntoBridge

企业遗留系统 AI 连接与业务本体平台。把授权来源文件与只读数据库，转换为可追溯、可查询、可审批的制造业业务能力。

**当前交付：可运行的制造业 MVP（0.1.0），不是全部 V1.0 路线图。** 默认使用合成 ERP / MES / WMS 数据，提供七个中文工作台、真实持久化 API 和官方 MCP SDK stdio 服务。无需 LLM 密钥。

## 本机启动

需要 Python 3.11+、[uv](https://docs.astral.sh/uv/) 与 Node.js 22。

```bash
cd /Volumes/ExtSSD/Github/ontobridge
uv sync --locked
npm --prefix web ci
npm --prefix web run build
uv run ontobridge --port 8787
```

打开 <http://127.0.0.1:8787>。API 文档：<http://127.0.0.1:8787/api/docs>。后台存储和演示来源位于 `var/`，刷新或重启不会丢失业务数据。首次启动创建 8 个订单、52 个业务对象、62 条确定性引用和 7 条已确认映射。演示来源的交期相对于首次初始化的企业业务日期生成，运行多天后会自然过期，不会伪造“最新同步”。

默认演示模式仅允许回环地址和本机请求。左下角可切换 FDE 工程师、PMC 审批人和只读访客。演示会话使用进程内随机令牌，重启后自动重新登录。**不要用演示模式承载真实企业数据。**

开发时可用 `npm --prefix web run dev`，访问 `http://127.0.0.1:5173`，API 仍运行在 8787。

## 已实现

| 工作台 | 可执行功能 |
| --- | --- |
| 总览 | 实时聚合来源、本体与能力数量；订单搜索、风险筛选、订单全景 |
| 数据源 | SQLite `mode=ro` + `query_only`；CSV；真实结构发现、样例行、手动同步 |
| 映射 | 字段选择、逐行 Pydantic 校验、预览、版本记录、人工确认后生效 |
| 本体 | 7 类固定制造对象、6 类关系、可缩放图谱、Canonical ID、实例、来源及历史版本 |
| 身份关联 | 仅明确业务引用自动关联；缺失引用进入人工复核；来源变更使旧人工纠正失效 |
| 业务能力 | 7 个 REST / MCP 工具、统一规则、来源血缘、时效警告、角色授权 |
| 动作审批 | 预检、幂等创建、独立审批人、驳回、输入版本复核、导出草案；永不写回源系统 |
| 运维 | 同步成功/失败批次、变更/移除数量、操作与越权审计、JSON 导出 |

核心对象：SalesOrder、Product、WorkOrder、Operation、Resource、Material、Inventory。

能力：`list_due_orders`、`get_order_360`、`check_delivery_risk`、`get_material_shortage`、`get_production_progress`、`simulate_schedule`、`propose_schedule_change`。

## 5 分钟验收路线

1. 总览打开 `SO-2026-0088`，检查关联的两个 MES 工单、完工数、交付估算和来源。
2. 数据源中对 ERP 执行“发现结构”和“立即同步”；重复同步变更数为 0。
3. 映射中编辑并预览；保存后先确认映射，再同步数据。期间风险会显示待核实。
4. 业务本体查看对象实例及版本，导出定义或血缘。
5. AI 业务能力运行订单全景、物料缺口和产能情景比较；结果包含规则版本与 provenance。
6. FDE 角色提交排产草案，切换 PMC 角色填写依据并审批；状态为“已批准草案”，`executed=false`。
7. 运维中查看相应批次和审计事件。

## 接入自己的授权来源

将静态导出文件放在 `var/sources/`（可用 `ONTOBRIDGE_SOURCE_ROOT` 更换）。SQLite 连接面向经授权的导出数据库/专用只读副本；CSV 为 UTF-8，支持 BOM。UI 填相对路径，系统拒绝目录逃逸、符号链接越界、未知表和任意 SQL。

推荐从如下 CSV 开始：

```csv
code,title,measure
MAT-004,纽扣,粒
```

创建 CSV 数据源后，把 `code → id`、`title → name`、`measure → unit` 映射到 Material。预览、保存、确认、同步。UI 映射左侧是标准字段，右侧选来源字段。

每个来源最多 10,000 行；CSV 最大 10 MiB；SQLite 查询设置三秒执行预算。同步为**完整快照**：整批校验成功才提交，重复执行不产生重复对象，快照缺失记录产生删除历史。失败保留上次快照并把风险标为待核实。业务主键跨来源冲突时拒绝整批，不做猜测合并。同步期间应保持导出文件静止；建议用临时文件生成完成后原子替换。

## 规则口径

`delivery_risk_v1` 是可测试的确定性估算：

- 生产进度取工单累计完工量；订单量与拆单总量必须一致。
- 产品在 MVP 中包含一个主物料及单耗。剩余需求 = 未完工数量 × 单耗。
- 同物料库存按交期、订单号顺序分配一次；资源工时按相同顺序排队，工序工时求和。
- 每日固定可用工时，按自然日估算；企业业务日期默认为 `Asia/Shanghai`，可配置 `ONTOBRIDGE_TIMEZONE`。
- 任一缺料或估算延期为高风险；缺失、过期、失败来源、未确认映射或未解决关联为 `unknown`。
- 不含节假日、换线、物料在途、质检损耗或复杂多级 BOM。情景比较不是最优排程，预计完成日期不含缺料到货等待。
- 来源时效依据**导出文件修改时间**，不是原系统业务事件时间；同步时间另存。跨来源并非分布式一致快照。客户接入需提供真实更新水位和业务校验。

## 真实权限模式 / 私有部署

参见 [部署说明](docs/deployment.md)。非演示模式无匿名访问、无角色切换入口。每个网关仅服务一个企业租户、一个 API worker；存储查询始终带 tenant 条件。该隔离不等于已实现企业组织行列级权限继承。

```bash
python3 scripts/create_local_env.py
mkdir -p imports
# 把授权导出文件放入 imports，再启动服务（不会自动导入样例）。
docker compose up --build -d
```

生成脚本以 0600 权限保存 `.env`、`var/access.json`，不打印令牌，拒绝覆盖已有凭据。Compose 使用 PostgreSQL 16、只读来源挂载、本机绑定端口、非 root API 进程。登录时从本机 `var/access.json` 选择对应令牌。

## MCP

使用[官方 Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk)的 stdio 传输。所有调用回到同一 API，避免绕过权限、审计和业务规则。

```json
{
  "mcpServers": {
    "ontobridge": {
      "command": "uv",
      "args": ["--directory", "/Volumes/ExtSSD/Github/ontobridge", "run", "ontobridge-mcp"],
      "env": {
        "ONTOBRIDGE_API_URL": "http://127.0.0.1:8787",
        "ONTOBRIDGE_API_TOKEN": "填写配置的企业只读或 operator 令牌"
      }
    }
  }
}
```

MCP 不暴露审批工具。Agent 可提出草案，由控制台的人类审批人审核。客户端超时为 30 秒；超时后草案状态应通过同一幂等键重试确认，不应认定执行成功。

## 验证

```bash
uv run pytest -q
uv run ruff check backend tests scripts
npm --prefix web run build
# 先启动默认演示 API，再验证真实 MCP stdio 握手、发现、调用和权限拒绝：
uv run python scripts/mcp_smoke.py
```

[验收记录](docs/verification.md)包含当前已执行的验证及尚未执行的部署验证。

## 目录

```text
backend/ontobridge/    API、连接器、对象校验、业务规则、存储、MCP
web/                  React + TypeScript + React Flow 控制台
tests/                API 集成、规则与数据一致性回归测试
scripts/              MCP 烟测、私有部署凭据生成
var/                  本地持久数据（已忽略）
docs/                 架构、部署和验收说明
```

## 后续范围

未实现 PostgreSQL/MySQL/Oracle **来源驱动**、CDC/增量水位、调度器、断线队列、Excel/SFTP、RPA 录制、工业协议、可编辑通用本体元模型、多主物料 BOM、求解器、SSO、密钥库、组织行列权限、远程网关控制面、GraphQL、SQL View、事件订阅及生产执行。PostgreSQL 在此版本是平台存储选项，不能误作已经支持 PostgreSQL ERP 采集。上线前仍需客户授权来源、真实订单对账、性能验收及企业权限集成。
