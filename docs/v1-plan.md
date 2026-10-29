# OntoBridge V1.0｜企业 AI 连接与本体平台开发计划

研发实施方案 · 12 周 · 三大核心工作台 · 面向真实 ERP/MES 集成

我建议直接以12 周完成可在真实制造企业部署的 V1.0 为目标，采用“统一技术底座 + 三个独立研发模块 + 一条贯穿全程的业务验证链路”的方式。

项目不是开发三个彼此独立的工具，而是要形成完整的产品闭环：

01 · 无 API 数据源连接器设计器

建立连接 → 发现结构 → 采集与同步 → 生成数据资产

02 · AI 辅助本体映射工作台

字段识别 → 业务实体映射 → 跨系统关联 → 本体发布

03 · 业务能力自动生成器

能力定义 → 自动生成 REST / MCP → 测试 → 发布与治理

企业智能问数

PMC 辅助排产

交期风险分析

## 一、首先确定 V1.0 的研发边界

| 项目       | V1.0 必须支持                        | 后续版本               |
| -------- | -------------------------------- | ------------------ |
| 数据库连接    | SQL Server、MySQL、PostgreSQL 只读   | Oracle、其他商业数据库     |
| 无 API 接入 | Excel/CSV、SFTP、网页 ERP 的授权 RPA 导出 | Windows 原生 ERP 自动化 |
| 数据同步     | 全量、增量轮询、失败重试                     | 大规模 CDC、多节点调度      |
| AI 语义映射  | 字段识别、映射候选、人工确认                   | 行业知识持续学习           |
| 业务本体     | 对象、属性、关系、规则、版本                   | 复杂图推理、本体市场         |
| 能力生成     | REST API、MCP Tools、Schema、测试     | GraphQL、事件能力目录     |
| 企业权限     | 租户隔离、角色权限、数据权限、审计                | 集团级统一身份治理          |
| 业务执行     | 只读查询、动作草案                        | 审批后受控回写            |

这里有个关键决定：V1.0 不做通用 RPA 平台，也不做完整知识图谱平台，更不做一套新的 ERP。

我们只开发把遗留系统变成 AI 可使用的业务服务所必需的功能。

在真实客户中，第一条验证链路固定为：

ERP 销售订单 + MES 生产工单 + 物料库存 → 统一订单本体 → 订单全景查询与交期风险分析 → MCP Tool。

这样可以不断验证开发进度，而不是三个模块都完成后才发现无法打通。

## 二、12 周整体开发排期

研发路线图

W1–W12

基础平台与数据模型

W1–W2

连接器设计器

W2–W6

本体映射工作台

W3–W8

业务能力自动生成器

W5–W10

集成测试与真实企业试点

W9–W12

| 周期     | 研发任务                        | 阶段交付      |
| ------ | --------------------------- | --------- |
| W1–2   | 产品底座、数据模型、身份权限、部署           | 平台可启动     |
| W3–4   | 数据源管理、连接测试、结构发现、同步引擎        | ERP 数据可入库 |
| W5–6   | AI 字段映射、标准实体、主键关联、RPA 导出    | 本体初步可用    |
| W7–8   | 关系设计、指标规则、数据质量、版本发布         | 订单本体可查询   |
| W9–10  | API/MCP 工具工厂、自动测试、权限网关      | Agent 能调用 |
| W11–12 | 真实 ERP/MES 适配、稳定性、安全测试、试点验收 | V1.0 可交付  |

排期假设：已有一家具备授权测试条件的试点企业，研发团队约 5–7 名工程人员，并由 FDE/业务顾问持续参与。若涉及无法取得数据库权限的 Windows 原生老 ERP，需要单独评估适配工作量。

## 三、三大工作台的具体研发任务

### A. 无 API 数据源连接器设计器

## Connector Studio

核心目标：让实施人员不需要为每家企业手工编写一套数据采集程序。

优先级 P0

| 任务 ID  | 开发内容       | 验收成果                             |
| ------ | ---------- | -------------------------------- |
| CON-01 | 连接器插件 SDK  | 可以注册和加载不同类型连接器                   |
| CON-02 | 数据库连接向导    | SQL Server/MySQL/PostgreSQL 真实连接 |
| CON-03 | 数据结构发现     | 展示表、字段、类型、主键候选                   |
| CON-04 | 文件接入工作流    | CSV/Excel/SFTP 导入                |
| CON-05 | 增量同步引擎     | 游标、checkpoint、重复记录处理             |
| CON-06 | 网页 RPA 连接器 | 录制授权导出流程，采集报表                    |
| CON-07 | 运行监控       | 同步任务、异常告警、日志追踪                   |

关键技术决策： 第一版自行实现统一连接器接口，数据库同步引擎可参考 Apache NiFi 的增量采集机制；CDC 作为独立扩展，不要强制依赖。因为 SQL Server CDC 等方案通常需要在源数据库开启相关功能和权限。

[image](https://www.google.com/s2/favicons?domain=https://nifi.apache.org\&sz=32)

Apache NiFi+1



针对网页 ERP，Playwright 已提供操作录制、元素定位和等待机制，可以用于构建受限的 Web RPA 连接器。

[image](https://www.google.com/s2/favicons?domain=https://playwright.dev\&sz=32)

Playwright+1



### B. AI 辅助本体映射工作台

## Ontology Studio

核心目标：把不同系统的原始记录转换为有统一身份、业务关系和规则约束的企业对象。

核心差异化

| 任务 ID  | 开发内容              | 验收成果          |
| ------ | ----------------- | ------------- |
| ONT-01 | 制造业标准本体 DSL       | 对象、属性、关系、约束   |
| ONT-02 | AI 字段映射助手         | 源字段自动推荐、解释    |
| ONT-03 | 可视化本体画布           | 拖拽对象、编辑关系     |
| ONT-04 | Entity Resolution | 源系统身份映射、冲突处理  |
| ONT-05 | 业务规则引擎            | 状态、枚举、单位、指标规则 |
| ONT-06 | 本体质量检测            | 孤儿记录、重复键、错误关联 |
| ONT-07 | 版本发布              | 审批、依赖检测、回滚    |

这里开发难度最大的是 ONT-04。

建议独立设计三张核心逻辑表：

```
canonical_entities
  canonical_id
  tenant_id
  entity_type

source_identity_aliases
  canonical_id
  source_id
  source_primary_key

entity_relationships
  from_canonical_id
  to_canonical_id
  relation_type
  evidence_ref
  mapping_version
```

例如从 ERP 查询 `SO-2026-0088`，先解析其 Canonical ID，再查询经确认的工单关系，而不是由 LLM 临时猜测 SQL Join。

AI 主要参与候选生成、字段解释和异常分析。正式映射发布必须经过规则验证与人工批准。

### C. 业务能力自动生成器

## Capability Factory

核心目标：将经过验证的业务本体转为可以直接被 AI Agent 调用的标准服务。

商业化出口

| 任务 ID  | 开发内容         | 验收成果          |
| ------ | ------------ | ------------- |
| CAP-01 | 业务能力 DSL     | 配置入参、对象、业务规则  |
| CAP-02 | 能力执行引擎       | 基于批准的查询模板执行   |
| CAP-03 | OpenAPI 生成器  | 自动生成 REST API |
| CAP-04 | MCP Tool 生成器 | 自动发布标准工具      |
| CAP-05 | 测试沙箱         | 自动生成测试与调用预览   |
| CAP-06 | 发布管理         | 权限、版本、回滚      |
| CAP-07 | 调用监控         | 日志、延迟、错误、审计   |

MCP 适配建议按 2026 年 7 月 28 日正式发布的协议版本设计，同时验证旧客户端兼容性。该版本采用无状态核心，减少了 MCP 服务横向扩展时的会话管理负担。

[image](https://www.google.com/s2/favicons?domain=https://blog.modelcontextprotocol.io\&sz=32)

Model Context Protocol Blog+1



## 四、三个模块共用的技术底座

为了控制研发成本，我建议 V1.0 优先采用 TypeScript 技术体系，减少跨语言维护。

| 技术层     | 推荐方案                         |
| ------- | ---------------------------- |
| 前端      | Next.js + React + TypeScript |
| 可视化编辑   | React Flow                   |
| 平台后端    | NestJS                       |
| 本地连接网关  | Node.js Worker               |
| 数据库驱动   | MSSQL、MySQL、PostgreSQL 驱动    |
| AI 映射   | 可配置 LLM + 结构化 JSON Schema    |
| 核心数据库   | PostgreSQL                   |
| 文件与快照存储 | MinIO                        |
| 异步任务    | Redis + BullMQ               |
| MCP 服务  | MCP TypeScript SDK v2        |
| 部署      | Docker Compose，后续 Kubernetes |

React Flow 很适合本体关系画布，因为它原生提供可定制节点、连线、缩放和拖拽等功能。

[image](https://www.google.com/s2/favicons?domain=https://reactflow.dev\&sz=32)

React Flow



代码架构建议从模块化单体起步，同时把 Local Gateway 和 MCP Gateway 做成独立运行进程。未来复杂审批和长时间执行的业务操作，可以引入 Temporal；当前没有必要为简单采集任务提前引入复杂分布式编排。

[image](https://www.google.com/s2/favicons?domain=https://docs.temporal.io\&sz=32)

Temporal Documentation



## 五、首批 API 契约

三个工作台必须从第一天使用统一的 API 契约，而不是分别开发后再对接。

| 接口                                        | 作用               |
| ----------------------------------------- | ---------------- |
| `POST /api/v1/sources`                    | 创建数据源            |
| `POST /api/v1/sources/{id}/test`          | 测试连接             |
| `POST /api/v1/sources/{id}/discover`      | 发现表结构            |
| `POST /api/v1/pipelines/{id}/runs`        | 启动同步             |
| `POST /api/v1/mappings/suggestions`       | 生成 AI 映射候选       |
| `POST /api/v1/mappings/{id}/approve`      | 批准字段映射           |
| `POST /api/v1/ontologies/{id}/publish`    | 发布本体版本           |
| `POST /api/v1/capabilities/{id}/generate` | 生成业务能力           |
| `POST /api/v1/capabilities/{id}/test`     | 执行沙箱测试           |
| `POST /api/v1/capabilities/{id}/publish`  | 发布 REST / MCP 服务 |

核心原则是：业务能力生成器只能消费已经发布的本体版本。不允许调用未经审核的映射草案，更不能让 LLM 生成任意 SQL 直接访问生产数据库。

## 六、V1.0 最终验收演示

建议把下面的完整流程作为正式验收标准：

## ERP → MES → Ontology → MCP

示例：销售订单 SO-2026-0088

1. 连接真实 ERP 数据源，并发现销售订单表。
2. 连接 MES 数据源，获取相关生产工单与进度。
3. 由 AI 提出字段映射候选，业务人员审核确认。
4. 建立销售订单与生产工单的一对多关联，检查数据血缘。
5. 选择订单全景业务模板，自动生成 `get_order_360`。
6. 完成 API/MCP 沙箱测试、权限校验并发布。
7. Agent 查询订单交付状态，返回订单、工单、进度、风险和来源。
8. 模拟源系统断连、数据过期或关联冲突，验证错误处理及审计。

验收不能只看界面是否可用，还要验证三个关键结果：业务数据能与源系统对账、跨系统关系有明确证据、REST 和 MCP 输出的业务含义与权限控制保持一致。

## 七、完整研发计划文件

我将进一步细化的研发内容整理成了可以直接供研发团队、FDE 或 AI Coding 工具使用的 Markdown 文件。

OntoBridge V1.0 完整研发计划

Markdown · 约 478 行 · 包含开发 Goal

内容包含系统架构、三个模块的功能任务、数据库表设计、Connector SPI、Ontology DSL、Capability DSL、API 契约、12 周任务安排、团队职责、测试矩阵及可以交给 Codex 的统一开发 Goal。\
下载完整研发开发计划.md

建议的实际启动方式： 不要让团队先分别开发三个工作台。第一周先完成统一数据库模型、Connector SPI、Ontology DSL 和 Capability DSL 的契约设计；第二周建立带模拟 ERP/MES 数据的完整可运行环境。随后三个模块并行开发，始终以同一条订单业务链路进行集成测试。

这样最终交付的就不是三个孤立的软件模块，而是一个真正能够作为制造业 AI 应用底座的 OntoBridge V1.0。