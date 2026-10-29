# 参与贡献

欢迎帮助 OntoBridge 改进遗留系统连接、制造业本体、Agent 能力与开发体验。

开始前请阅读 [README](README.md)、[V1 计划](docs/v1-plan.md) 和 [LICENSE](LICENSE)。本项目是采用非商业许可的源码可用项目；贡献默认按照本项目同一许可提供。你仍保留自己贡献的版权，没有在此要求版权转让或另行商业再许可。提交前请确认你有权贡献相关内容。

## 有价值的贡献

- 可复现的问题报告及回归修复。
- 带只读权限验证和合成测试数据的 ERP/MES 连接器。
- 有明确关联证据、指标定义和反例的行业本体模板。
- 权限、数据质量、故障恢复和跨协议一致性测试。
- 控制台可用性、文档与部署体验改进。

较大的架构改动、新依赖或新业务模板，建议先在仓库 Issue 中说明问题、使用场景和方案，再提交实现。不确定需求归属时，可以通过 [FDE前线](https://fdeopen.com) 联系作者。

## 本地开发

环境准备见 [快速开始](README.md#quickstart)。如果使用前端开发服务器，Vite 当前将 `/api` 代理到 `127.0.0.1:8787`，因此请在一个终端启动对应的开发 API，在另一个终端启动 Vite：

```bash
ONTOBRIDGE_DATA=var/v1-console uv run ontobridge --port 8787
# 另一个终端
npm --prefix web run dev
```

需要同步采集时仍需启动 Worker；若同时运行 MCP，将 `ONTOBRIDGE_API_URL` 设为 `http://127.0.0.1:8787`。不使用 Vite 的构建后演示流程继续使用 README 中的 8788 端口。

更改后运行与变更相关的检查；完整常用检查为：

```bash
uv run ruff check backend tests scripts
uv run pytest -q
npm --prefix web run build
npm --prefix gateway test
```

真实数据库、私有容器与浏览器验收脚本见 README。它们使用专用合成环境；不得指向客户生产库。

## 提交要求

1. 一次变更聚焦一个问题，描述触发条件、修改后的行为和验证结果。
2. 新行为或错误修复应有针对性的测试；纯文档修改检查命令与链接即可。
3. 涉及数据模型、配置、API 或操作流程时，同步更新文档。
4. 保持租户隔离、来源只读、独立审核、来源证据与失败拒绝原则。
5. 不提交真实企业数据、数据库文件、令牌、密钥、模型请求内容或私有访问地址。
6. 不新增任意 SQL、任意脚本执行或未经治理的生产回写入口。

连接器通过 `ontobridge.connectors` entry point 注册；接口与实施约束见 [Connector SDK](docs/v1-operations.md#connector-sdk)。贡献连接器时，说明上游版本、授权前提、同步边界及已验证的平台。

## 报告问题

普通问题请提供版本或提交号、部署方式、最小复现步骤、预期与实际行为，以及经过脱敏的错误信息。数据问题请使用最小合成样例。

安全漏洞请按 [安全政策](SECURITY.md) 私下报告，不在公开 Issue 中披露可利用细节或真实凭据。
