import { useEffect, useState } from "react";
import {
  Database,
  FileSpreadsheet,
  Play,
  Plus,
  RefreshCw,
  ShieldCheck,
  Workflow,
  X,
  Download,
  Code2,
  Activity,
} from "lucide-react";

type Json = any;
type API = (path: string, body?: Json) => Promise<Json>;
const statuses: Record<string, string> = {
  queued: "排队中",
  running: "同步中",
  retry_wait: "等待重试",
  failed: "失败",
  succeeded: "已完成",
  configured: "已配置",
  connected: "已连接",
  error: "异常",
  draft: "待审批",
  approved: "已批准",
};
const time = (v?: string) => (v ? new Date(v).toLocaleString("zh-CN") : "—");
const kinds: Record<string, string> = {
  postgresql: "PostgreSQL",
  mysql: "MySQL",
  mssql: "SQL Server",
  sqlite: "SQLite",
  csv: "CSV",
  excel: "Excel / XLSX",
  sftp: "SFTP",
  web_rpa: "网页 RPA",
};

export default function IntegrationStudio({
  api,
  principal,
  demo,
}: {
  api: API;
  principal: Json;
  demo: boolean;
}) {
  const edit = ["operator", "admin"].includes(principal.role);
  const approve = ["approver", "admin"].includes(principal.role);
  const [data, setData] = useState<Json>({
    connectors: [],
    sources: [],
    pipelines: [],
    runs: [],
    operations: { workers: [], alerts: [] },
    workflows: [],
  });
  const [tab, setTab] = useState("sources"),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false),
    [modal, setModal] = useState("");
  const [source, setSource] = useState<Json>({
    name: "",
    system: "ERP",
    kind: "postgresql",
    connection_ref: "",
    path: "",
    schema_name: "",
    tables: [],
    freshness_minutes: 60,
    options: {},
  });
  const [pluginOptions, setPluginOptions] = useState("{}");
  const [tables, setTables] = useState<Json[]>([]),
    [selectedSource, setSelectedSource] = useState<Json>(null),
    [detail, setDetail] = useState<Json>(null);
  const [pipeline, setPipeline] = useState<Json>({
    name: "",
    source_id: "",
    table: "",
    primary_key: "",
    mode: "full",
    cursor: null,
    batch_size: 500,
    poll_seconds: 0,
    max_attempts: 3,
    retry_seconds: 5,
    enabled: true,
  });
  const [workflowJSON, setWorkflowJSON] = useState(
    JSON.stringify(
      {
        name: "订单报表导出",
        connection_ref: "mock-erp-web",
        expected_columns: ["id", "customer"],
        output_format: "csv",
        steps: [
          { action: "navigate", path: "/" },
          { action: "download", selector: "#export" },
        ],
      },
      null,
      2,
    ),
  );
  async function load() {
    if (edit) {
      const [sources, pipelines, runs, operations, workflows, connectors] =
        await Promise.all(
          [
            "/v1/sources",
            "/v1/pipelines",
            "/v1/runs",
            "/v1/operations",
            "/v1/rpa/workflows",
            "/v1/connectors",
          ].map((path) => api(path)),
        );
      setData({ sources, pipelines, runs, operations, workflows, connectors });
    }
    if (!edit && approve) {
      const workflows = await api("/v1/rpa/workflows");
      setData((d: Json) => ({ ...d, workflows }));
      setTab("rpa");
    }
  }
  useEffect(() => {
    let active = true;
    const reload = async () => {
      try {
        if (active) await load();
      } catch (e) {
        if (active) setError((e as Error).message);
      }
    };
    void reload();
    const id = setInterval(reload, 5000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, [principal.user]);
  async function run(task: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await task();
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function discover(s: Json) {
    setSelectedSource(s);
    const result = await api(`/v1/sources/${s.id}/discover`, {});
    setTables(result);
    return result;
  }
  async function openPipeline(s: Json) {
    await run(async () => {
      const result = await discover(s);
      const t = result[0];
      const pk =
        t?.primary_key?.[0] ||
        t?.columns.find((c: Json) => c.primary_key_candidate)?.name ||
        "";
      setPipeline({
        name: s.name + " · " + (t?.name || ""),
        source_id: s.id,
        table: t?.name || "",
        primary_key: pk,
        mode: "full",
        cursor: null,
        batch_size: 500,
        poll_seconds: 0,
        max_attempts: 3,
        retry_seconds: 5,
        enabled: true,
      });
      setModal("pipeline");
    });
  }
  const workerOnline = data.operations.workers.some(
    (w: Json) => Date.now() - new Date(w.last_seen).getTime() < 90000,
  );
  const table = tables.find((t) => t.name === pipeline.table);
  if (!edit && !approve)
    return (
      <div className="info-strip">
        <ShieldCheck />
        <div>
          <strong>需要配置权限</strong>
          <span>
            只读访客通过已发布业务能力查询；来源配置与原始数据资产仅对工程师开放。
          </span>
        </div>
      </div>
    );
  return (
    <div className="v1-studio">
      <div className="info-strip">
        <Workflow size={20} />
        <div>
          <strong>Connector Studio · V1</strong>
          <span>
            接入定义 → 连接测试 → 结构发现 → 持久化流水线 →
            数据资产。只有完整成功的批次会对下游可见。
          </span>
        </div>
        <span className={"badge " + (workerOnline ? "connected" : "draft")}>
          {workerOnline ? "网关在线" : "等待网关 Worker"}
        </span>
      </div>
      {error && (
        <div className="alert" role="alert">
          {error}
          <button onClick={() => setError("")} aria-label="关闭连接器错误">
            <X size={14} />
          </button>
        </div>
      )}
      {notice && (
        <div className="all-clear" role="status">
          {notice}
        </div>
      )}
      <div className="studio-toolbar">
        <div className="tabs">
          {[
            ["sources", "数据源"],
            ["pipelines", "同步流水线"],
            ["runs", "运行与告警"],
            ["rpa", "网页导出流程"],
          ]
            .filter(([id]) => edit || id === "rpa")
            .map(([id, label]) => (
              <button
                key={id}
                className={tab === id ? "selected" : ""}
                onClick={() => setTab(id)}
              >
                {label}
              </button>
            ))}
        </div>
        <button onClick={() => void run(load)} disabled={busy}>
          <RefreshCw size={14} />
          刷新
        </button>
      </div>
      {tab === "sources" && (
        <>
          <div className="studio-actions">
            {demo && (
              <button
                disabled={
                  busy ||
                  data.sources.some((s: Json) => s.name === "MOCK · ERP V1")
                }
                onClick={() =>
                  void run(async () => {
                    for (const s of [
                      {
                        name: "MOCK · ERP V1",
                        system: "ERP",
                        kind: "sqlite",
                        path: "erp.sqlite",
                      },
                      {
                        name: "MOCK · MES 工单 V1",
                        system: "MES",
                        kind: "csv",
                        path: "mes_work_orders.csv",
                      },
                      {
                        name: "MOCK · MES 工序 V1",
                        system: "MES",
                        kind: "csv",
                        path: "mes_operations.csv",
                      },
                      {
                        name: "MOCK · WMS V1",
                        system: "WMS",
                        kind: "csv",
                        path: "wms_stock.csv",
                      },
                    ])
                      await api("/v1/sources", s);
                    setNotice(
                      "已注册 4 个明确标注的 mock 来源。可测试连接、发现结构并创建流水线。",
                    );
                  })
                }
              >
                <Code2 size={15} />
                注册本地 mock 来源
              </button>
            )}
            <button className="primary" onClick={() => setModal("source")}>
              <Plus size={15} />
              新建连接
            </button>
          </div>
          <div className="source-grid">
            {data.sources.map((s: Json) => (
              <section className="panel source-card" key={s.id}>
                <div className="source-card-top">
                  <span className="source-icon">
                    {["csv", "excel", "sftp"].includes(s.kind) ? (
                      <FileSpreadsheet />
                    ) : (
                      <Database />
                    )}
                  </span>
                  <span className={"badge " + s.status}>
                    {statuses[s.status] || s.status}
                  </span>
                </div>
                <span className="eyebrow">
                  {s.system} / {kinds[s.kind] || s.kind}
                </span>
                <h2>{s.name}</h2>
                <p className="mono source-path">
                  {s.connection_ref ? "凭据引用：" + s.connection_ref : s.path}
                </p>
                <p className="source-time">最近成功：{time(s.synced_at)}</p>
                {s.last_error && <p className="error-text">{s.last_error}</p>}
                <footer>
                  <button
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        const result = await api(
                          `/v1/sources/${s.id}/test`,
                          {},
                        );
                        setNotice(`连接测试通过，发现 ${result.tables} 张表。`);
                      })
                    }
                  >
                    测试连接
                  </button>
                  <button
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        await discover(s);
                        setModal("schema");
                      })
                    }
                  >
                    发现结构
                  </button>
                </footer>
                <button
                  className="primary studio-wide-button"
                  disabled={busy}
                  onClick={() => void openPipeline(s)}
                >
                  建立同步流水线 <Workflow size={15} />
                </button>
              </section>
            ))}
          </div>
          {!data.sources.length && (
            <div className="empty">
              <Database size={32} />
              <p>添加真实连接定义，或先注册本地 mock 来源。</p>
            </div>
          )}
        </>
      )}
      {tab === "pipelines" && (
        <section className="panel">
          <div className="panel-heading">
            <h2>已配置的同步流水线</h2>
            <span>{data.pipelines.length} 条</span>
          </div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>流水线 / 来源表</th>
                  <th>模式 / 轮询</th>
                  <th>游标</th>
                  <th>最近成功</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {data.pipelines.map((p: Json) => (
                  <tr key={p.id}>
                    <td>
                      <strong>{p.name}</strong>
                      <small>
                        {p.table} · {p.primary_key}
                      </small>
                    </td>
                    <td>
                      {p.mode === "incremental" ? "增量轮询" : "全量快照"}
                      <small>
                        {p.poll_seconds
                          ? p.poll_seconds + " 秒轮询"
                          : "手动触发"}
                      </small>
                    </td>
                    <td className="mono">
                      {p.cursor?.column || "—"}
                      <small>
                        {p.checkpoint
                          ? JSON.stringify(p.checkpoint)
                          : "尚无 checkpoint"}
                      </small>
                    </td>
                    <td>{time(p.last_success_at)}</td>
                    <td>
                      <div className="row-actions">
                        <button
                          disabled={busy}
                          onClick={() =>
                            void run(async () => {
                              const r = await api(
                                `/v1/pipelines/${p.id}/runs`,
                                {},
                              );
                              setNotice("任务已入队：" + r.id);
                              setTab("runs");
                            })
                          }
                        >
                          <Play size={13} />
                          运行
                        </button>
                        <button
                          disabled={busy}
                          onClick={() =>
                            void run(async () => {
                              setDetail(
                                await api("/v1/assets?pipeline_id=" + p.id),
                              );
                              setModal("assets");
                            })
                          }
                        >
                          数据资产
                        </button>
                        {p.mode === "incremental" && (
                          <button
                            disabled={busy}
                            onClick={() =>
                              void run(async () => {
                                await api(`/v1/pipelines/${p.id}/runs`, {
                                  force_full: true,
                                });
                                setNotice(
                                  "已创建全量对账任务，可同步来源删除记录。",
                                );
                                setTab("runs");
                              })
                            }
                          >
                            全量对账
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!data.pipelines.length && (
            <div className="empty">
              <Workflow />
              <p>从数据源“建立同步流水线”开始。</p>
            </div>
          )}
        </section>
      )}
      {tab === "runs" && (
        <>
          <div className="info-strip">
            <Activity size={18} />
            <div>
              <strong>独立 Local Gateway</strong>
              <span>
                启动命令：uv run
                ontobridge-worker。队列、暂存批次和重试状态保存在平台数据库中。
              </span>
            </div>
          </div>
          {data.operations.alerts
            .filter((a: Json) => a.status === "open")
            .map((a: Json) => (
              <div className="alert" key={a.id}>
                {a.message} · {a.run_id}
              </div>
            ))}
          <section className="panel">
            <div className="panel-heading">
              <h2>同步任务与 checkpoint</h2>
            </div>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>任务 / 时间</th>
                    <th>状态</th>
                    <th>尝试次数</th>
                    <th>批次 / 暂存行</th>
                    <th>变更 / 移除</th>
                    <th>详情</th>
                  </tr>
                </thead>
                <tbody>
                  {data.runs.map((r: Json) => (
                    <tr key={r.id}>
                      <td>
                        <strong className="mono">{r.id.slice(0, 18)}…</strong>
                        <small>{time(r.created_at)}</small>
                      </td>
                      <td>
                        <span className={"badge " + r.status}>
                          {statuses[r.status]}
                        </span>
                      </td>
                      <td>{r.attempts}</td>
                      <td>
                        {r.batch_count} / {r.staged}
                      </td>
                      <td>
                        {r.changed ?? "—"} / {r.removed ?? "—"}
                      </td>
                      <td>
                        <button
                          onClick={() => {
                            setDetail(r);
                            setModal("run");
                          }}
                        >
                          查看日志
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
      {tab === "rpa" && (
        <>
          <div className="info-strip">
            <ShieldCheck size={18} />
            <div>
              <strong>受限的网页报表导出</strong>
              <span>
                录制只读查询与下载步骤，不执行任意脚本。流程需独立审批；网关拒绝越过授权站点和只读页面范围。
              </span>
            </div>
          </div>
          <div className="studio-actions">
            {edit && (
              <button className="primary" onClick={() => setModal("rpa")}>
                <Plus size={15} />
                导入录制流程
              </button>
            )}
          </div>
          <details className="panel mcp-guide">
            <summary>打开本地录制器</summary>
            <p>
              管理员在连接配置文件中设置 base_url 和
              allowed_read_paths，再运行下列命令，由授权用户完成查询和下载。输出
              JSON 导入此工作台。
            </p>
            <pre className="json">{`uv run ontobridge-record --tenant ${principal.tenant} --connection mock-erp-web --columns id,customer --output var/recording.json`}</pre>
          </details>
          <section className="panel studio-gap">
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>流程 / 连接</th>
                    <th>步骤</th>
                    <th>状态</th>
                    <th>录制者</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {data.workflows.map((w: Json) => (
                    <tr key={w.id}>
                      <td>
                        <strong>{w.name}</strong>
                        <small>{w.connection_ref}</small>
                      </td>
                      <td>{w.steps.length}</td>
                      <td>
                        <span className={"badge " + w.status}>
                          {statuses[w.status]}
                        </span>
                      </td>
                      <td>{w.created_by}</td>
                      <td>
                        <div className="row-actions">
                          <button
                            onClick={() => {
                              setDetail(w);
                              setModal("run");
                            }}
                          >
                            审阅步骤
                          </button>
                          <button
                            disabled={
                              busy ||
                              !approve ||
                              w.created_by === principal.user ||
                              w.status === "approved"
                            }
                            onClick={() =>
                              void run(async () => {
                                await api(
                                  `/v1/rpa/workflows/${w.id}/approve`,
                                  {},
                                );
                                setNotice("导出流程已批准，可绑定网页数据源。");
                              })
                            }
                          >
                            批准
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
      {modal && (
        <div className="overlay" onClick={() => setModal("")}>
          <section
            className="modal wide"
            role="dialog"
            aria-modal="true"
            aria-label="连接器配置"
            onClick={(e) => e.stopPropagation()}
          >
            <header>
              <h2>
                {
                  {
                    source: "新建 V1 数据源",
                    pipeline: "配置同步流水线",
                    schema: "来源结构与主键",
                    assets: "已提交数据资产",
                    run: "运行与流程详情",
                    rpa: "导入网页导出录制",
                  }[modal]
                }
              </h2>
              <button aria-label="关闭配置" onClick={() => setModal("")}>
                <X size={18} />
              </button>
            </header>
            <div className="modal-body">
              {error && <div className="alert">{error}</div>}
              {modal === "source" && (
                <form
                  id="source-v1"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void run(async () => {
                      const value = {
                        ...source,
                        options: kinds[source.kind] ? source.options : JSON.parse(pluginOptions),
                        path: source.path || null,
                        connection_ref: source.connection_ref || null,
                        schema_name: source.schema_name || null,
                      };
                      await api("/v1/sources", value);
                      setModal("");
                      setNotice("连接定义已保存，请测试连接。");
                    });
                  }}
                >
                  <div className="form-grid">
                    <label>
                      数据源名称
                      <input
                        required
                        value={source.name}
                        onChange={(e) =>
                          setSource({ ...source, name: e.target.value })
                        }
                      />
                    </label>
                    <label>
                      系统类型
                      <select
                        value={source.system}
                        onChange={(e) =>
                          setSource({ ...source, system: e.target.value })
                        }
                      >
                        {["ERP", "MES", "WMS", "OTHER"].map((v) => (
                          <option key={v}>{v}</option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <div className="form-grid">
                    <label>
                      连接器
                      <select
                        value={source.kind}
                        onChange={(e) =>
                          setSource({ ...source, kind: e.target.value })
                        }
                      >
                        {data.connectors.map(({kind: k}: Json) => (
                          <option key={k} value={k}>
                            {kinds[k] || k}
                          </option>
                        ))}
                      </select>
                    </label>
                    {!["sqlite", "csv", "excel"].includes(source.kind) && (
                      <label>
                        管理员凭据引用
                        <input
                          required
                          placeholder="例如 erp-readonly"
                          value={source.connection_ref}
                          onChange={(e) =>
                            setSource({
                              ...source,
                              connection_ref: e.target.value,
                            })
                          }
                        />
                      </label>
                    )}
                  </div>
                  {["sqlite", "csv", "excel", "sftp"].includes(source.kind) && (
                    <label>
                      授权目录下的来源路径
                      <input
                        required
                        placeholder="erp.sqlite / orders.xlsx"
                        value={source.path}
                        onChange={(e) =>
                          setSource({ ...source, path: e.target.value })
                        }
                      />
                    </label>
                  )}
                  {["postgresql", "mssql", "mysql"].includes(source.kind) && (
                    <label>
                      Schema（可选）
                      <input
                        value={source.schema_name}
                        onChange={(e) =>
                          setSource({ ...source, schema_name: e.target.value })
                        }
                      />
                    </label>
                  )}
                  {source.kind === "web_rpa" && (
                    <label>
                      已批准导出流程
                      <select
                        required
                        value={source.options.workflow_id || ""}
                        onChange={(e) =>
                          setSource({
                            ...source,
                            options: { workflow_id: e.target.value },
                          })
                        }
                      >
                        <option value="">选择流程</option>
                        {data.workflows
                          .filter((w: Json) => w.status === "approved")
                          .map((w: Json) => (
                            <option key={w.id} value={w.id}>
                              {w.name}
                            </option>
                          ))}
                      </select>
                    </label>
                  )}
                  {!kinds[source.kind] && <label>插件选项（JSON，凭据只填 connection_ref）<textarea value={pluginOptions} onChange={e => setPluginOptions(e.target.value)} /></label>}
                  <label>
                    来源时效 SLA（分钟）
                    <input
                      type="number"
                      min="1"
                      value={source.freshness_minutes}
                      onChange={(e) =>
                        setSource({
                          ...source,
                          freshness_minutes: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                  <p className="muted">
                    文件位于授权根目录的当前租户子目录。数据库密码、SFTP
                    主机指纹和网页登录态只保存在管理员配置文件，不能写入此表单。
                  </p>
                </form>
              )}
              {modal === "pipeline" && (
                <form
                  id="pipeline-v1"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void run(async () => {
                      await api("/v1/pipelines", pipeline);
                      setModal("");
                      setTab("pipelines");
                      setNotice("流水线已保存。点击运行后由独立 Worker 执行。");
                    });
                  }}
                >
                  <label>
                    流水线名称
                    <input
                      required
                      value={pipeline.name}
                      onChange={(e) =>
                        setPipeline({ ...pipeline, name: e.target.value })
                      }
                    />
                  </label>
                  <div className="form-grid">
                    <label>
                      来源表
                      <select
                        value={pipeline.table}
                        onChange={(e) => {
                          const t = tables.find(
                            (t) => t.name === e.target.value,
                          );
                          setPipeline({
                            ...pipeline,
                            table: e.target.value,
                            primary_key: t.primary_key[0] || "",
                          });
                        }}
                      >
                        {tables.map((t) => (
                          <option key={t.name}>{t.name}</option>
                        ))}
                      </select>
                    </label>
                    <label>
                      唯一主键
                      <select
                        required
                        value={pipeline.primary_key}
                        onChange={(e) =>
                          setPipeline({
                            ...pipeline,
                            primary_key: e.target.value,
                            cursor: pipeline.cursor
                              ? {
                                  ...pipeline.cursor,
                                  tie_breaker: e.target.value,
                                }
                              : null,
                          })
                        }
                      >
                        <option value="">选择具有唯一约束的字段</option>
                        {table?.columns.map((c: Json) => (
                          <option key={c.name}>{c.name}</option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <div className="form-grid">
                    <label>
                      同步模式
                      <select
                        value={pipeline.mode}
                        onChange={(e) =>
                          setPipeline({
                            ...pipeline,
                            mode: e.target.value,
                            cursor:
                              e.target.value === "full"
                                ? null
                                : {
                                    column: "",
                                    tie_breaker: pipeline.primary_key,
                                    kind: "datetime",
                                  },
                          })
                        }
                      >
                        <option value="full">全量快照（含删除对账）</option>
                        {["postgresql", "mysql", "mssql", "sqlite"].includes(
                          selectedSource?.kind,
                        ) && <option value="incremental">增量轮询</option>}
                      </select>
                    </label>
                    <label>
                      轮询间隔（秒，0 为手动）
                      <input
                        type="number"
                        min="0"
                        value={pipeline.poll_seconds}
                        onChange={(e) =>
                          setPipeline({
                            ...pipeline,
                            poll_seconds: Number(e.target.value),
                          })
                        }
                      />
                    </label>
                  </div>
                  {pipeline.mode === "incremental" && (
                    <div className="form-grid">
                      <label>
                        增量水位字段
                        <select
                          required
                          value={pipeline.cursor.column}
                          onChange={(e) =>
                            setPipeline({
                              ...pipeline,
                              cursor: {
                                ...pipeline.cursor,
                                column: e.target.value,
                              },
                            })
                          }
                        >
                          <option value="">选择更新时间或递增版本</option>
                          {table?.columns
                            .filter(
                              (c: Json) => c.name !== pipeline.primary_key,
                            )
                            .map((c: Json) => (
                              <option key={c.name}>{c.name}</option>
                            ))}
                        </select>
                      </label>
                      <label>
                        水位类型
                        <select
                          value={pipeline.cursor.kind}
                          onChange={(e) =>
                            setPipeline({
                              ...pipeline,
                              cursor: {
                                ...pipeline.cursor,
                                kind: e.target.value,
                              },
                            })
                          }
                        >
                          <option value="datetime">时间戳</option>
                          <option value="integer">整数版本</option>
                          <option value="string">可排序字符串</option>
                        </select>
                      </label>
                    </div>
                  )}
                  <div className="form-grid">
                    <label>
                      每批读取行数
                      <input
                        type="number"
                        min="1"
                        max="5000"
                        value={pipeline.batch_size}
                        onChange={(e) =>
                          setPipeline({
                            ...pipeline,
                            batch_size: Number(e.target.value),
                          })
                        }
                      />
                    </label>
                    <label>
                      最大尝试次数
                      <input
                        type="number"
                        min="1"
                        max="10"
                        value={pipeline.max_attempts}
                        onChange={(e) =>
                          setPipeline({
                            ...pipeline,
                            max_attempts: Number(e.target.value),
                          })
                        }
                      />
                    </label>
                  </div>
                  <p className="muted">
                    增量列必须在变更时更新。物理删除通过全量对账处理；上次水位边界会重读并去重。
                  </p>
                </form>
              )}
              {modal === "schema" &&
                tables.map((t) => (
                  <section className="studio-gap" key={t.name}>
                    <h3>{t.name}</h3>
                    <table>
                      <thead>
                        <tr>
                          <th>字段</th>
                          <th>类型</th>
                          <th>主键</th>
                          <th>可空</th>
                        </tr>
                      </thead>
                      <tbody>
                        {t.columns.map((c: Json) => (
                          <tr key={c.name}>
                            <td>{c.name}</td>
                            <td>{c.type}</td>
                            <td>
                              {c.primary_key
                                ? "已声明"
                                : c.primary_key_candidate
                                  ? "候选"
                                  : "—"}
                            </td>
                            <td>{c.nullable ? "是" : "否"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <details>
                      <summary>样例记录</summary>
                      <pre className="json">
                        {JSON.stringify(t.sample, null, 2)}
                      </pre>
                    </details>
                  </section>
                ))}
              {["assets", "run"].includes(modal) && (
                <pre className="json">{JSON.stringify(detail, null, 2)}</pre>
              )}
              {modal === "rpa" && (
                <>
                  <label>
                    录制器生成的 Workflow JSON
                    <textarea
                      className="dsl-editor"
                      value={workflowJSON}
                      onChange={(e) => setWorkflowJSON(e.target.value)}
                    />
                  </label>
                  <p className="muted">
                    导入后处于待审批状态，另一位审批人批准前无法执行。也可在此手工编写同一受限步骤契约。
                  </p>
                </>
              )}
            </div>
            <footer>
              <button onClick={() => setModal("")}>关闭</button>
              {modal === "source" && (
                <button
                  type="submit"
                  form="source-v1"
                  className="primary"
                  disabled={busy}
                >
                  保存连接定义
                </button>
              )}
              {modal === "pipeline" && (
                <button
                  type="submit"
                  form="pipeline-v1"
                  className="primary"
                  disabled={busy}
                >
                  保存流水线
                </button>
              )}
              {modal === "rpa" && (
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() =>
                    void run(async () => {
                      await api("/v1/rpa/workflows", JSON.parse(workflowJSON));
                      setModal("");
                      setNotice("录制流程已保存，等待独立审批。");
                    })
                  }
                >
                  <Download size={14} />
                  导入待审批流程
                </button>
              )}
            </footer>
          </section>
        </div>
      )}
    </div>
  );
}
