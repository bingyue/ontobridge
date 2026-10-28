# 验收记录

日期：2026-10-29（Asia/Shanghai）。源码位于 `/Volumes/ExtSSD/Github/ontobridge`。

## 已执行并通过

- `uv run pytest -q`：16 项测试通过。
  - 七个业务能力、来源血缘、订单进度和产能口径。
  - SQLite 只读约束、重复同步幂等、批次失败原子回滚与失败审计。
  - 映射预览、确认、版本变更使旧数据失效。
  - 多订单共享库存不重复使用、来源文件过期时拒绝草案。
  - 幂等键冲突、并发重复请求仅生成一个草案。
  - operator/viewer/approver 角色限制，admin 也不能审批自己的申请。
  - 输入数据变化阻断批准；仍可驳回。
  - 缺失业务引用人工修正；来源变化后要求重新复核。
  - 删除与重新出现连续保留版本历史。
  - 目录逃逸、符号链接越界、非法 CSV、重复主键与行数边界拒绝。
  - 新建 CSV 来源 → 映射 → 确认 → 同步；业务主键碰撞拒绝。
  - 持久化与 tenant 查询隔离；生产配置禁止无凭据启动。
- `uv run ruff check backend tests scripts`：通过。
- `npm --prefix web run build`：TypeScript 检查及 Vite 生产构建通过。
- `uv run python scripts/mcp_smoke.py`：通过官方 SDK 初始化/工具发现；发现 7 个工具；6 个读取工具真实调用成功；viewer 提交草案被拒绝。
- Docker 多阶段镜像完整构建通过（Node 22 / Python 3.13，非 root 运行）。
- 隔离 Compose 项目 `ontobridge-validation` + PostgreSQL 16：真实鉴权、关闭演示登录、CSV 结构发现、映射确认、快照同步、本体查询均通过。
- 重启 API 容器后，PostgreSQL 对象仍可查询；匿名查询返回 401；演示登录返回 404。
- 仅测试用 Compose 容器、网络、卷已清理；未修改其他 Docker 项目。

测试依赖中的 Starlette 针对 httpx TestClient 发出一条弃用警告，未影响当前用例；未忽略或屏蔽测试失败。

## 浏览器验收

使用真实 Chromium / Playwright CLI。

- 总览正常载入 4 个来源、52 个对象、7 项能力、62 条关联。
- 进入能力工作台，FDE 提交排产草案；切换 PMC 角色，填写依据并审批。记录显示 `approved_draft`，`executed=false`。
- 映射中打开销售订单字段；真实预览后保存 v2，旧快照出现时效/版本警告；确认映射并同步后恢复正常。
- React Flow 本体关系图、业务实例和数据版本正常展示。
- 1440×1100 桌面布局及 390×844 手机布局已查看；手机 document.scrollWidth = innerWidth = 390，表格内部可横向滚动。
- 首次加载发现缺少 favicon 的 404；已补充本地 SVG favicon，重新加载后无该错误。
- 对话框支持 Escape、初始焦点、Tab 焦点循环及关闭后的焦点恢复。

截图保存在本地 `output/playwright/`（运行产物，Git 忽略）。

## 部署环境发现

当前 Docker context 为 Colima，ExtSSD 未共享到虚拟机，第一次只读 bind mount 看不到外置盘文件。增加 `ONTOBRIDGE_IMPORTS` 配置后，用主目录中的专用测试目录完成接入与持久化验证。未更改 Colima 全局设置。详见 deployment.md。

## 验收边界

本次使用合成 ERP/MES/WMS 与隔离测试 CSV，没有连接或修改用户现有生产系统，也没有执行生产业务动作。未执行真实客户历史订单对账、跨企业组织行列权限、复杂 BOM 排程或大规模吞吐测试。新增的 GitHub CI 工作流已写入，但未推送或在 GitHub 上运行。
