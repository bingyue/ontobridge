# 部署与运行

## 单机演示

`uv run ontobridge` 会创建本地 SQLite 平台存储和合成来源。数据库连接本身是只读；只有首次演示数据生成器创建样例文件。API 同时提供构建后的控制台。演示仅在回环地址运行。默认角色切换属于演示功能。

样例不会每天自动刷新，避免把旧业务快照伪装成新事实。重新演示请设置新的 `ONTOBRIDGE_DATA` 目录启动，旧数据保留不删除。

## 非演示配置

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| ONTOBRIDGE_DEMO | 1 | 设为 0 关闭演示数据和演示登录 |
| ONTOBRIDGE_DATA | var | 平台本地目录 |
| ONTOBRIDGE_SOURCE_ROOT | DATA/sources | 明确授权的来源文件目录 |
| ONTOBRIDGE_DATABASE_URL | DATA 内 SQLite | 可配置 PostgreSQL + psycopg |
| ONTOBRIDGE_TOKENS | 空 | JSON：token → user / role / tenant；非演示必填 |
| ONTOBRIDGE_TIMEZONE | Asia/Shanghai | 企业交期日期时区 |
| ONTOBRIDGE_API_URL | http://127.0.0.1:8787 | MCP 连接的 API |
| ONTOBRIDGE_API_TOKEN | 无 | MCP API 凭据 |

令牌至少 24 字符。权限角色：viewer 只读业务查询；operator 配置来源、映射、同步、关联和提交草案；approver 审批；admin 兼具各角色权限，但仍不能审批自己创建的草案。同租户用户均能读取业务对象和审计信息。不同租户应使用独立网关和来源目录。浏览器令牌存于 sessionStorage，关闭会话后消失；实际 SSO 与令牌轮换需后续接入。

本地无 Docker 的真实权限运行：执行凭据脚本后，在受信终端加载 `.env`（不要输出变量）：

```bash
set -a
source .env
set +a
export ONTOBRIDGE_DEMO=0
export ONTOBRIDGE_DATA=var/private
export ONTOBRIDGE_SOURCE_ROOT="$PWD/imports"
uv run ontobridge --port 8788
```

## Docker Compose

Compose 启动 PostgreSQL 与 API（单 worker），端口只绑定 127.0.0.1。首次创建 `.env` 后执行 `docker compose up --build -d`。`imports/` 只读挂载到 `/data/sources`，应用用户 UID 10001 需要对文件有读取权限。数据库不向宿主机开放端口。反向代理 HTTPS、SSO、备份与网络策略由部署环境配置；不要将明文 HTTP 和管理令牌直接暴露到公网。

`docker compose logs console` 查看进程日志，控制台运维页查看业务审计。`docker compose down` 保留命名卷；不要带 `-v`，除非明确要删除业务数据。

## 持久化与恢复

平台使用 SQLAlchemy documents 表，主键为 `(tenant, kind, key)`。每个同步批次、动作变更与对应审计在同一事务中提交。API 单进程 RLock 序列化读写，当前版本不能水平扩展多 worker。PostgreSQL 迁移是连接配置层兼容，尚无分布式锁和集群调度。

SQLite 备份应先停止网关，再备份平台 SQLite 文件及授权来源目录。PostgreSQL 使用 `pg_dump` 与标准恢复流程。重新运行同一来源同步为幂等快照；失败批次不会部分写入，重试会重新读取完整快照。并未实现日志级 CDC 或中途断点水位。

## 来源与规则限制

SQLite 适配器只允许发现普通表，参数化来源必须位于授权目录；表名根据实际发现结果验证并引用。查询有执行时间预算和行数上限；此版本适合静态导出或专用只读副本，不能将“只读”视为对任意生产库零负载。CSV 必须有唯一非空表头、完整列数和不重复主键。

企业项目验收必须完成：实际授权、字段逐项确认、真实跨系统引用对账、源数据业务时间与同步 SLA、真实订单回测、角色和数据范围验证。当前合成样例证明的是软件流程与规则行为，不是特定 ERP 厂商兼容性。

## 外置磁盘与 Colima

部分 Colima 配置只共享用户主目录，未共享 `/Volumes/ExtSSD`。此时镜像能构建，但 bind mount 的文件可能在容器内不可见。不要误判为连接器错误。可以自行配置 Colima 的外置磁盘共享，或者将**授权导入文件**放到已共享目录，并覆盖来源挂载：

```bash
# 目录须由你准备并放入授权导出文件；项目代码仍在 ExtSSD。
ONTOBRIDGE_IMPORTS=/已共享的/授权导入目录 ONTOBRIDGE_PORT=8788 docker compose up --build -d
```

`ONTOBRIDGE_PORT` 仅改变本机端口，容器内部保持 8787。验证时使用了隔离的 Compose 项目与测试来源目录，没有修改 Colima 的全局挂载配置。
