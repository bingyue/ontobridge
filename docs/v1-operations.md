# V1 部署与运维手册

本手册对应已完成 mock 验收的 V1 代码；逐项结果见 [v1-status.md](v1-status.md)。当前试点只使用合成数据。真实企业试点需另行授权与对账。

## 进程和持久化

API/控制台、Local Gateway Worker、MCP Gateway 是三个独立进程。API 和 Worker 必须共享 `ONTOBRIDGE_DATABASE_URL`、`ONTOBRIDGE_SOURCE_ROOT`、`ONTOBRIDGE_CONNECTIONS_FILE`、`ONTOBRIDGE_CACHE_ROOT`；只有 API 需要模型配置。Worker 从元数据库查出租户和流水线，不需要用户登录令牌。

- 元数据库：生产部署使用 PostgreSQL；开发默认 SQLite。统一记录表以 `(tenant, kind, id)` 为主键；所有业务事务绑定认证租户。PostgreSQL 使用租户事务锁，SQLite 使用 `BEGIN IMMEDIATE`，适合当前单网关规模。
- 持久化运行：60 秒租约、按批保存 staging；仅在所有批次成功后原子提交资产和 checkpoint。重复请求同一活跃流水线返回相同 run。故障按配置退避重试，耗尽后失败告警；后续成功自动解除告警。
- 全量同步对账删除；增量使用 `(watermark, unique primary key)`，重新读取最后水位边界。来源更新必须推进可靠水位。历史水位回填、物理删除不由轮询 CDC 猜测，需定期 `force_full` 对账。
- 文件 20 MiB、100,000 行、256 列；XLSX 只接收静态值，拒绝公式及过大压缩包。文件指纹变化会拒绝混合批次并从头重试。
- 文件/SFTP/RPA 时效基于文件修改时间；数据库采集基于实际观察时间。旧快照不会仅因为 Worker 心跳而变新，必要时全量采集并刷新本体。
- 数据库全量轮询的各批次不构成长事务一致性快照。并发更新频繁的来源应提供授权只读导出表/一致性副本，并核对高水位契约。

## 凭据与租户

`ONTOBRIDGE_DEMO=0` 时必须配置 `ONTOBRIDGE_TOKENS`，JSON 结构为令牌到主体的映射：

```json
{
  "replace-with-random-token-at-least-24-characters": {
    "user": "fde.factory-a", "tenant": "factory-a", "role": "operator"
  }
}
```

角色为 `viewer`、`operator`、`approver`、`admin`。管理员仍不能批准自己创建的映射、身份合并、RPA 流程、能力或动作草案。最后修改本体的人不能发布该草案。租户从认证主体取得，客户端请求头不能选择其他租户。

私有模式关闭旧 MVP 业务 API，默认仅显示三个 V1 工作台。演示模式保留旧页面，不能混入真实令牌。平台目前使用静态服务令牌，轮换后重启 API；没有宣称提供企业 SSO。

可在主体上增加行列授权：

```json
{
  "data_policy": {
    "objects": {
      "SalesOrder": {
        "rows": {"customer": ["授权客户"]},
        "columns": ["id", "customer", "due_date"],
        "provenance": false
      }
    }
  }
}
```

未列对象默认禁止。`rows` 不同字段按 AND 组合，每个允许值列表按 OR；空 rows 表示该对象全部行，`columns: "*"` 表示全部列。来源主键/血缘可能敏感，需显式授予 `provenance`。受限主体只能查看能力目录和调用已发布能力，不能走原始资产/配置/运维入口。关系端点、关联字段及中间单据同样受权限约束。

生产风险、库存分摊等制造模板依赖共享队列和库存，部分行列权限下会拒绝计算，避免返回缺失数据的“完整”结果。此类主体可以使用受限 `entity_lookup`。

## 来源配置

连接器不会从网页表单接收密码。管理员将以下结构写入 `config/connections.json`，权限 **0600**，容器内由 **UID 10001** 拥有。目录只读挂载到 `/run/ontobridge`。部署前检查容器实际 UID 可读；不能通过放宽至全员可读绕过检查。macOS/Colima 的绑定挂载路径必须在虚拟机共享范围内；外置盘不可见时使用显式共享或受控命名卷导入。

```json
{
  "factory-a": {
    "erp-reader": {
      "kind": "postgresql", "host": "erp.internal", "port": 5432,
      "database": "erp", "username": "ontobridge_reader",
      "password": "replace-locally", "sslmode": "verify-full"
    },
    "mes-reader": {
      "kind": "mysql", "host": "mes.internal", "port": 3306,
      "database": "mes", "username": "ontobridge_reader",
      "password": "replace-locally", "ssl_ca": "/run/ontobridge/mysql-ca.pem"
    },
    "sqlserver-reader": {
      "kind": "mssql", "host": "sql.internal", "port": 1433,
      "database": "ERP", "username": "ontobridge_reader",
      "password": "replace-locally", "encryption": "require"
    }
  }
}
```

来源配置只引用 `connection_ref: "erp-reader"`。`tables` 可限定表清单，`schema_name` 指定 PG schema/MSSQL schema。同步键必须具有全表单列 PRIMARY KEY 或 UNIQUE 约束/唯一索引；过滤（部分）唯一索引不满足要求。SQL Server 还检查目标表 INSERT/UPDATE/DELETE/ALTER/CONTROL 权限均不存在。所有数据库应使用来源端最小 SELECT 权限账号，驱动只读标记不能代替数据库授权。PostgreSQL/MySQL/SQLite 也启用只读事务/连接。

CSV/XLSX/SQLite 放在 `imports/<tenant>/`，UI 路径相对该租户目录。拒绝越界路径与越界符号链接。XLSX 每个工作表是一张表，CSV 表名为文件 stem。SFTP 配置示例：

```json
{
  "kind": "sftp", "host": "exports.internal", "port": 22,
  "username": "export-reader", "password": "replace-locally",
  "root": "/approved-exports", "known_hosts": "/run/ontobridge/known_hosts"
}
```

必须固定经过管理员核验的 SSH 主机公钥；不自动接受未知主机。来源 path 相对于远端 root，支持 CSV/XLSX。

## 网页 RPA

连接配置包含 `kind: "web_rpa"`、`base_url`、`allowed_read_paths`、可选 `allowed_login_paths`、`secrets`、可选 Playwright `storage_state`。仅同源 GET/HEAD 白名单与明确列出的登录 POST 允许通过。所有写业务操作均不在工作流指令中。

```json
{
  "kind": "web_rpa", "base_url": "https://erp.internal",
  "allowed_read_paths": ["/reports/orders", "/exports/*", "/assets/*"],
  "allowed_login_paths": ["/session/login"],
  "secrets": {"password": "replace-locally"}
}
```

在有图形桌面的授权操作机运行：

```bash
ONTOBRIDGE_CONNECTIONS_FILE=/absolute/path/connections.json \
uv run ontobridge-record --tenant factory-a --connection erp-web \
  --path /reports/orders --columns order_no,customer,qty --output var/order-export.json
```

人工完成筛选和下载。录制密码只保留 `secret_ref`，不保存明文。工作台“网页 RPA”导入生成 JSON，由另一位审批人批准，再创建 `web_rpa` 来源并选择该流程。首次 discover/test 会执行导出，Worker 每个 run 缓存一次，批次重试复用并核验指纹。导出写入 `ONTOBRIDGE_CACHE_ROOT/<tenant>/.exports/<run>`，与只读来源目录分离。验证码/MFA/复杂多页系统需授权人工处理并保存会话；当前不是通用浏览器自动化平台。

## 模型和发布

未配置模型时使用明确标注的 mock 候选。真实模型配置 `ONTOBRIDGE_LLM_CONFIG` 指向 0600 JSON 文件；Compose 中填容器内路径，例如 `/run/ontobridge/llm.json`。文件按租户配置：

```json
{
  "factory-a": {
    "provider": "openai_compatible", "base_url": "https://model.internal/v1",
    "model": "approved-model", "api_key": "replace-locally"
  }
}
```

模型只接收字段/类型/业务对象元数据，不发送源数据样例值。结构化 JSON 返回后仍校验存在的字段、允许的转换和类型；保存候选后独立人工批准。网络失败或非法结果不会自动降级为正式映射。

本体 DSL 支持属性 required/type/enum/minimum/exclusive_minimum/maximum/unit，跨字段 lte/lt/gte/gt/eq 约束，sum/count/ratio 指标。可视化编辑和高级 DSL 共用服务端校验。多对多必须配置真实 junction 对象和引用字段。相同名字/业务键不会由模型自动合并：先产生冲突，再人工指定权威来源，由独立审批人批准。

版本冻结 DSL、映射、身份决议及摘要；刷新仅按该版本生成新快照。质量失败保留旧快照，并阻止能力把它当作可信当前事实。身份撤销仅影响新草案，已发布版本保留原决议，可发布修正版并重新绑定能力。回滚只能选历史已发布版本。

## REST / MCP 与动作

- `GET /api/v1/generated/openapi.json`：按调用角色过滤的 OpenAPI。
- `GET /api/v1/tools`：相同目录的 MCP 描述。
- `POST /api/v1/invoke/{name}`：请求体直接为生成的入参。
- MCP：`http://127.0.0.1:8790/mcp`，使用相同平台 Bearer 身份，通过同一权限/执行/审计函数。
- 网关环境：`ONTOBRIDGE_API_URL`、`ONTOBRIDGE_MCP_HOST`、`ONTOBRIDGE_MCP_PORT`、`ONTOBRIDGE_MCP_ALLOWED_HOSTS`（逗号分隔）。远程提供服务时由 TLS 反向代理固定 Host，不允许任意 Origin；平台令牌仅用于本平台网关与后端。
- 官方 SDK v2 实现 [2026-07-28 协议](https://ts.sdk.modelcontextprotocol.io/v2/)，兼容旧客户端初始化。新协议不依赖应用会话存储。
- 排产模板只产生幂等审批草案；审批后仍 `executed=false`。没有 ERP/MES 写回端点。
- 超时是执行时间预算与超时拒绝返回，当前不提供计算线程的硬抢占；网关 HTTP 超时 35 秒。需要严格 CPU 隔离时应使用进程级资源限制。

## 备份、恢复和巡检

保存 PostgreSQL 逻辑备份、`gateway-data`（导出缓存）、来源导出和管理员凭据文件。执行备份前暂停 API/Worker 对元数据的修改，确保来源快照与数据库备份对应；恢复时使用新的隔离项目验证版本与业务对账，再切换服务。不要把 `.env`、访问令牌、模型密钥和连接配置提交到 Git。

巡检 `/api/v1/operations` 的 worker heartbeat、run、alert，以及 `/api/v1/capability-operations` 的错误率、延迟、调用记录。当前告警保存在控制台，没有未经授权发送外部通知。暂停与变更部署时可 `docker compose stop worker`，恢复 `docker compose start worker`；未提交批次在租约过期后接续。

隔离验收项目的管理命令（与正常部署不同）：

```bash
docker compose --env-file var/container-acceptance/.env \
  -p ontobridge-v1-acceptance -f compose.yaml \
  -f var/container-acceptance/compose.json ps
# 检查完成后可用相同参数执行 stop；不要加 -v 删除验收数据。
```

## Connector SDK

在已信任的 Python 包中继承 `ontobridge.v1.connector_sdk.Connector`，实现 `discover() -> list[TableSchema]`、`read(...) -> ReadBatch`，声明唯一 `kinds` 和 `incremental`。将插件放到 API 与 Worker 相同环境：

```toml
[project.entry-points."ontobridge.connectors"]
my_connector = "my_package:MyConnector"
```

重启后注册表加载插件，UI 来源类型列表来自注册表，未知/冲突 kind 拒绝创建。凭据通过 `ConnectorContext.profile()` 获取租户专属配置；文件使用 `context.path()` 做边界校验。插件是可信部署代码，不支持通过浏览器上传执行。插件 read 必须仅 SELECT/read，稳定排序、明确 exhausted、返回可序列化 checkpoint，并提供真实 `source_updated_at` 与 `freshness_basis`。测试包含通过模拟 entry point 注册新类型后经公开 API 完成采集的用例。

## 本机 SQL Server 验收环境

Apple Silicon 上的功能验收使用独立 QEMU x86-64 全系统模拟。该环境只放合成表，不作为生产或性能基准；生产部署使用微软支持的 Linux x86-64 主机。已安装 Homebrew `qemu` 与 `lima-additional-guestagents`。VM 文件保留在 ExtSSD，运行/停止显式指定独立 profile，不切换默认 Docker context：

```bash
COLIMA_HOME=/Volumes/ExtSSD/ob-v1-vm \
COLIMA_CACHE_HOME=/Volumes/ExtSSD/ob-v1-vm/cache \
colima start --profile ontobridge-x86 --activate=false
docker --context colima-ontobridge-x86 start ontobridge-v1-mssql
DOCKER_CONTEXT=colima-ontobridge-x86 \
uv run python scripts/v1_database_integration.py mssql --startup-timeout 1800
COLIMA_HOME=/Volumes/ExtSSD/ob-v1-vm colima stop --profile ontobridge-x86
```

验收后已停止此专用 VM 以释放 CPU/内存，磁盘和容器保留。真实数据库连接回执位于 `var/v1-integration/*-receipt.json`，无凭据快照已收录到 `docs/v1-database-receipts.json`。脚本不会自动删除或替换退出的容器；先查看对应 context 的日志，再决定恢复。
