import React, { useEffect, useState, useRef } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  ArrowUpRight,
  Box,
  Boxes,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleHelp,
  Clock3,
  Code2,
  Database,
  FileSpreadsheet,
  GitBranch,
  Layers3,
  LayoutDashboard,
  LoaderCircle,
  Network,
  Play,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Terminal,
  Unplug,
  Workflow,
  X,
} from "lucide-react";
import {
  Background,
  Controls,
  ReactFlow,
  MarkerType,
  type Node,
  type Edge,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./style.css";

type Json = any;
type Page =
  | "overview"
  | "sources"
  | "mappings"
  | "ontology"
  | "capabilities"
  | "actions"
  | "operations";
const nav: { id: Page; label: string; en: string; icon: typeof Database }[] = [
  { id: "overview", label: "工作总览", en: "Overview", icon: LayoutDashboard },
  { id: "sources", label: "数据源连接", en: "Connectors", icon: Database },
  { id: "mappings", label: "语义映射", en: "Mapping", icon: GitBranch },
  { id: "ontology", label: "业务本体", en: "Ontology", icon: Network },
  { id: "capabilities", label: "AI 业务能力", en: "Capabilities", icon: Boxes },
  { id: "actions", label: "动作与审批", en: "Action broker", icon: Workflow },
  { id: "operations", label: "网关与运维", en: "Operations", icon: Activity },
];
const statusText: Record<string, string> = {
  high: "高风险",
  low: "正常",
  unknown: "待核实",
  connected: "已连接",
  configured: "待同步",
  error: "连接异常",
  success: "同步成功",
  failed: "同步失败",
  confirmed: "已确认",
  draft: "待确认",
  pending_approval: "待审批",
  approved_draft: "已批准草案",
  rejected: "已驳回",
};
const dateTime = (v?: string) =>
  v
    ? new Date(v).toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "尚未同步";
const Badge = ({ value }: { value: string }) => (
  <span className={`badge ${value}`}>
    <i />
    {statusText[value] || value}
  </span>
);
const Empty = ({ text }: { text: string }) => (
  <div className="empty">
    <Box size={30} />
    <p>{text}</p>
  </div>
);
const JsonView = ({ value }: { value: Json }) => (
  <pre className="json">{JSON.stringify(value, null, 2)}</pre>
);
function Modal({
  title,
  children,
  close,
  wide = false,
}: {
  title: string;
  children: React.ReactNode;
  close: () => void;
  wide?: boolean;
}) {
  const modalRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const targets = () =>
      Array.from(
        modalRef.current?.querySelectorAll<HTMLElement>(
          "button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href]",
        ) || [],
      );
    targets()[0]?.focus();
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      if (e.key === "Tab") {
        const nodes = targets();
        const first = nodes[0],
          last = nodes[nodes.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", handler);
    return () => {
      document.removeEventListener("keydown", handler);
      document.body.style.overflow = overflow;
      previous?.focus();
    };
  }, []);

  return (
    <div className="overlay" onClick={close}>
      <section
        ref={modalRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={"modal " + (wide ? "wide" : "")}
        onClick={(e) => e.stopPropagation()}
      >
        <header>
          <div>
            <span className="eyebrow">ONTOBRIDGE WORKSPACE</span>
            <h2>{title}</h2>
          </div>
          <button className="icon-button" aria-label="关闭" onClick={close}>
            <X size={20} />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}
function App() {
  const [page, setPage] = useState<Page>("overview"),
    [config, setConfig] = useState<Json>(null),
    [principal, setPrincipal] = useState<Json>(null);
  const [token, setToken] = useState(sessionStorage.getItem("ob-token") || ""),
    [loginToken, setLoginToken] = useState("");
  const [data, setData] = useState<Json>(null),
    [error, setError] = useState(""),
    [toast, setToast] = useState(""),
    [busy, setBusy] = useState(false);
  const [search, setSearch] = useState(""),
    [riskFilter, setRiskFilter] = useState("all"),
    [detail, setDetail] = useState<Json>(null),
    [dialog, setDialog] = useState("");
  const [schema, setSchema] = useState<Json[]>([]),
    [sourceForm, setSourceForm] = useState({
      name: "",
      system: "ERP",
      kind: "sqlite",
      path: "",
      freshness_minutes: 1440,
    });
  const [mapping, setMapping] = useState<Json>({
      source_id: "",
      table: "",
      entity: "SalesOrder",
      fields: {},
    }),
    [preview, setPreview] = useState<Json>(null);
  const [selectedEntity, setSelectedEntity] = useState("SalesOrder"),
    [selectedCapability, setSelectedCapability] = useState("get_order_360"),
    [result, setResult] = useState<Json>(null);
  const [args, setArgs] = useState({
    order_id: "SO-2026-0088",
    days: 7,
    extra_hours: 2,
    note: "根据交期风险，申请调整产线每日可用工时。",
  });
  const [selectedAction, setSelectedAction] = useState<Json>(null),
    [reason, setReason] = useState(""),
    [resolution, setResolution] = useState<Json>(null);
  async function api(path: string, body?: Json, authToken = token) {
    const response = await fetch("/api" + path, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        "Content-Type": "application/json",
        ...(authToken ? { Authorization: "Bearer " + authToken } : {}),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    const value = await response.json();
    if (!response.ok)
      throw new Error(
        typeof value.detail === "string"
          ? value.detail
          : JSON.stringify(value.detail),
      );
    return value;
  }
  async function refresh(t = token) {
    const [
      overview,
      sources,
      mappings,
      ontology,
      capabilities,
      actions,
      operations,
    ] = await Promise.all(
      [
        "/overview",
        "/sources",
        "/mappings",
        "/ontology",
        "/capabilities",
        "/actions",
        "/operations",
      ].map((p) => api(p, undefined, t)),
    );
    setData({
      overview,
      sources,
      mappings,
      ontology,
      capabilities,
      actions,
      operations,
    });
  }
  async function run(task: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await task();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function login(role: string) {
    await run(async () => {
      const session = await api("/demo-session/" + role, {});
      setToken(session.token);
      sessionStorage.setItem("ob-token", session.token);
      setPrincipal(session.principal);
      await refresh(session.token);
    });
  }
  useEffect(() => {
    void (async () => {
      try {
        const c = await api("/config");
        setConfig(c);
        if (token) {
          try {
            setPrincipal(await api("/me"));
            await refresh();
            return;
          } catch {
            sessionStorage.removeItem("ob-token");
            setToken("");
          }
        }
        if (c.demo) await login("operator");
      } catch (e) {
        setError((e as Error).message);
      }
    })();
  }, []);
  useEffect(() => {
    if (toast) {
      const id = setTimeout(() => setToast(""), 3500);
      return () => clearTimeout(id);
    }
  }, [toast]);
  const navigate = (next: Page) => {
    setPage(next);
    setSearch("");
    setError("");
  };
  const canEdit = principal && ["operator", "admin"].includes(principal.role);
  const canApprove =
    principal && ["approver", "admin"].includes(principal.role);
  async function sync(id: string) {
    await run(async () => {
      try {
        await api(`/sources/${id}/sync`, {});
        setToast("数据同步完成，已更新本体与数据血缘");
      } finally {
        await refresh();
      }
    });
  }
  async function chooseSource(id: string) {
    setMapping((m: Json) => ({ ...m, source_id: id, table: "", fields: {} }));
    setPreview(null);
    await run(async () => {
      setSchema(await api(`/sources/${id}/schema`));
    });
  }
  async function openOrder(id: string) {
    await run(async () => {
      setDetail(
        await api("/capabilities/get_order_360/call", { order_id: id }),
      );
      setDialog("order");
    });
  }
  async function invoke() {
    await run(async () => {
      const value = await api(`/capabilities/${selectedCapability}/call`, {
        ...args,
        idempotency_key: crypto.randomUUID(),
      });
      setResult(value);
      await refresh();
      if (selectedCapability === "propose_schedule_change")
        setToast("草案已创建，等待另一位业务负责人审批");
    });
  }
  const download = (value: Json, name: string) => {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  };
  if (!data)
    return (
      <div className="login">
        <div className="brand-symbol">
          <GitBranch />
        </div>
        <h1>
          OntoBridge<span>®</span>
        </h1>
        <p>连接遗留系统，让业务成为 AI 的语言。</p>
        {error && <div className="alert">{error}</div>}
        {config && !config.demo ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                setPrincipal(await api("/me", undefined, loginToken));
                setToken(loginToken);
                sessionStorage.setItem("ob-token", loginToken);
                await refresh(loginToken);
              });
            }}
          >
            <label>
              企业访问令牌
              <input
                type="password"
                value={loginToken}
                onChange={(e) => setLoginToken(e.target.value)}
                required
                autoComplete="off"
              />
            </label>
            <button className="primary" disabled={busy}>
              进入工作台 <ArrowRight size={16} />
            </button>
          </form>
        ) : (
          <div className="loading">
            <LoaderCircle className="spin" />
            正在连接本地网关…
          </div>
        )}
      </div>
    );
  const {
    overview,
    sources,
    mappings,
    ontology,
    capabilities,
    actions,
    operations,
  } = data;
  const filteredOrders = [...overview.orders]
    .sort(
      (a: Json, b: Json) =>
        a.due_date.localeCompare(b.due_date) || a.id.localeCompare(b.id),
    )
    .filter(
      (o: Json) =>
        (o.id + " " + o.customer + " " + o.product_id)
          .toLowerCase()
          .includes(search.toLowerCase()) &&
        (riskFilter === "all" || o.risk.level === riskFilter),
    );
  const high = overview.orders.filter(
    (o: Json) => o.risk.level === "high",
  ).length;
  const current = nav.find((n) => n.id === page)!;
  const nodes: Node[] = ontology.entities.map((e: Json, i: number) => ({
    id: e.id,
    position: [
      { x: 45, y: 90 },
      { x: 350, y: 30 },
      { x: 45, y: 270 },
      { x: 350, y: 270 },
      { x: 45, y: 450 },
      { x: 655, y: 90 },
      { x: 655, y: 270 },
    ][i],
    data: {
      label: (
        <div className="graph-node">
          <span>{e.id}</span>
          <strong>{e.label}</strong>
          <small>
            {e.count} 个业务对象 <ArrowUpRight size={13} />
          </small>
        </div>
      ),
    },
    className: selectedEntity === e.id ? "selected-node" : "",
  }));
  const edges: Edge[] = ontology.relations.map((r: Json, i: number) => ({
    id: String(i),
    source: r.source,
    target: r.target,
    label: r.label + " · " + r.cardinality,
    markerEnd: { type: MarkerType.ArrowClosed },
    style: { stroke: "#78a99a" },
    labelStyle: { fontSize: 10, fill: "#607970" },
    type: "smoothstep",
  }));
  const entity = ontology.entities.find((e: Json) => e.id === selectedEntity);
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            navigate("overview");
          }}
        >
          <div className="brand-symbol">
            <GitBranch size={23} />
          </div>
          <div>
            OntoBridge<span>ENTERPRISE AI INFRASTRUCTURE</span>
          </div>
        </a>
        <div className="workspace">
          <div className="workspace-icon">{config.demo ? "织" : "企"}</div>
          <div>
            <strong>
              {config.demo ? "华织制造 · 演示空间" : principal.tenant}
            </strong>
            <span>
              本地工作空间 <i />
            </span>
          </div>
          <ChevronRight size={15} />
        </div>
        <div className="nav-label">WORKSPACE / 工作台</div>
        <nav>
          {nav.map((n) => (
            <button
              key={n.id}
              aria-label={n.label}
              title={n.label}
              onClick={() => navigate(n.id)}
              className={page === n.id ? "active" : ""}
            >
              <n.icon size={19} />
              <span>{n.label}</span>
              {n.id === "actions" && overview.pending_actions > 0 ? (
                <b className="nav-count">{overview.pending_actions}</b>
              ) : (
                n.id === "overview" && <span className="nav-dot" />
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="gateway-indicator">
            <div>
              <span className="pulse" />
              Local Gateway <ArrowUpRight size={13} />
            </div>
            <p>数据留在企业，能力连接 AI。</p>
            <small>只读连接 · 草案审批</small>
          </div>
          <button
            className="help-button"
            onClick={() => {
              setDetail({
                version: "0.1.0",
                boundary:
                  "制造业 MVP：SQLite 只读 + CSV；本地全量快照同步；不含 RPA、CDC、生产写回。",
                docs: "/api/docs",
                rule: "按交期分配共享产能与库存；不含节假日、换线及在途。",
              });
              setDialog("help");
            }}
          >
            <CircleHelp size={17} />
            使用说明 <span>↗</span>
          </button>
          <div className="user">
            <div className="avatar">
              {principal.role === "approver" ? "P" : "F"}
            </div>
            <div>
              <strong>{principal.label || principal.user}</strong>
              <span>
                {principal.role} · {principal.tenant}
              </span>
            </div>
            {config.demo ? (
              <select
                aria-label="切换演示角色"
                value={principal.role}
                onChange={(e) => void login(e.target.value)}
                disabled={busy}
              >
                {config.profiles.map((p: Json) => (
                  <option key={p.role} value={p.role}>
                    {p.label}
                  </option>
                ))}
              </select>
            ) : (
              <button
                className="icon-button"
                aria-label="退出登录"
                onClick={() => {
                  sessionStorage.removeItem("ob-token");
                  location.reload();
                }}
              >
                <Unplug size={16} />
              </button>
            )}
          </div>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div>
            <span>工作台</span>
            <ChevronRight size={14} />
            <strong>{current.label}</strong>
          </div>
          <div className="topbar-right">
            <span className="private-tag">
              <ShieldCheck size={14} />
              本地部署
            </span>
            <span className="separator" />
            <span className="demo-tag">
              {config.demo ? "演示数据" : "企业数据"}
            </span>
            <button
              className="icon-button"
              aria-label="刷新数据"
              disabled={busy}
              onClick={() => void run(() => refresh())}
            >
              <RefreshCw size={17} className={busy ? "spin" : ""} />
            </button>
          </div>
        </header>
        <div className="content">
          <div className="page-heading">
            <div>
              <span className="eyebrow">
                {current.en.toUpperCase()} / ONTOBRIDGE
              </span>
              <h1>
                {page === "overview"
                  ? "让企业数据，成为业务能力。"
                  : current.label}
              </h1>
              <p>
                {
                  {
                    overview:
                      "从遗留系统到 AI Agent，一览企业业务连接的完整链路。",
                    sources:
                      "通过经过授权的本地连接，读取企业系统中的业务事实。",
                    mappings:
                      "逐项确认字段、主键与业务含义，让每次转换都有据可查。",
                    ontology:
                      "在真实单据引用之上，建立订单、生产与物料的关系。",
                    capabilities:
                      "同一套业务语义，通过 REST 与 MCP 提供给你的 Agent。",
                    actions:
                      "每一个业务建议，都经过预检、审批和明确的状态管理。",
                    operations: "查看本地网关状态、同步批次与完整操作记录。",
                  }[page]
                }
              </p>
            </div>
            <div className="heading-actions">
              {page === "overview" ? (
                <>
                  <button onClick={() => navigate("ontology")}>
                    <Network size={16} />
                    查看本体
                  </button>
                  <button
                    className="primary"
                    disabled={!canEdit}
                    onClick={() => {
                      navigate("sources");
                      setDialog("source");
                    }}
                  >
                    <Plus size={16} />
                    连接数据源
                  </button>
                </>
              ) : page === "sources" ? (
                <button
                  className="primary"
                  disabled={!canEdit}
                  onClick={() => setDialog("source")}
                >
                  <Plus size={16} />
                  连接数据源
                </button>
              ) : page === "mappings" ? (
                <button
                  className="primary"
                  disabled={!canEdit}
                  onClick={() => {
                    setMapping({
                      source_id: "",
                      table: "",
                      entity: "SalesOrder",
                      fields: {},
                    });
                    setSchema([]);
                    setPreview(null);
                    setDialog("mapping");
                  }}
                >
                  <Plus size={16} />
                  创建映射
                </button>
              ) : page === "operations" ? (
                <button
                  onClick={() =>
                    download(operations, "ontobridge-operations.json")
                  }
                >
                  <ArrowDownToLine size={16} />
                  导出运行记录
                </button>
              ) : null}
            </div>
          </div>
          {error && (
            <div role="alert" className="alert">
              <span>{error}</span>
              <button
                className="icon-button"
                onClick={() => setError("")}
                aria-label="关闭错误"
              >
                <X size={16} />
              </button>
            </div>
          )}
          {overview.warnings.length > 0 && (
            <div className="warning">
              <Clock3 size={17} />
              {overview.warnings.join("；")}
            </div>
          )}
          {page === "overview" && (
            <>
              <div className="stats-grid">
                {[
                  {
                    label: "已接入数据源",
                    value: overview.source_count,
                    unit: "个",
                    note: "SQLite / CSV · 两种接入模式",
                    icon: Database,
                  },
                  {
                    label: "统一业务对象",
                    value: overview.object_count,
                    unit: "条",
                    note: `${overview.entity_count} 类对象 · ${overview.relation_count} 条来源关联`,
                    icon: Network,
                  },
                  {
                    label: "已发布业务能力",
                    value: capabilities.length,
                    unit: "项",
                    note: "REST API + MCP · 同源业务规则",
                    icon: Boxes,
                  },
                  {
                    label: "需要关注的订单",
                    value: high,
                    unit: "单",
                    note: `${overview.orders.filter((o: Json) => o.risk.level === "unknown").length} 单待核实 · ${overview.pending_actions} 项待审批`,
                    icon: Activity,
                  },
                ].map((s, i) => (
                  <article className={"stat-card stat-" + i} key={s.label}>
                    <div>
                      <span>{s.label}</span>
                      <s.icon size={19} />
                    </div>
                    <strong>
                      {s.value}
                      <small>{s.unit}</small>
                    </strong>
                    <p>
                      {i === 3 ? (
                        <span className="amber-dot" />
                      ) : (
                        <span className="green-dot" />
                      )}
                      {s.note}
                    </p>
                  </article>
                ))}
              </div>
              <section className="panel pipeline-panel">
                <div className="panel-heading">
                  <div>
                    <span className="green-dot" />
                    <h2>业务连接链路</h2>
                    <span className="muted">从数据到行动</span>
                  </div>
                  <button
                    className="text-button"
                    onClick={() => navigate("operations")}
                  >
                    查看网关 <ArrowUpRight size={14} />
                  </button>
                </div>
                <div className="pipeline">
                  {[
                    {
                      title: "遗留业务系统",
                      sub: "ERP · MES · WMS",
                      icon: Database,
                      page: "sources",
                      count: `${overview.source_count} 个数据源`,
                    },
                    {
                      title: "数据与语义映射",
                      sub: "字段校验 · 身份关联",
                      icon: GitBranch,
                      page: "mappings",
                      count: `${mappings.length} 条映射`,
                    },
                    {
                      title: "企业业务本体",
                      sub: "对象 · 关系 · 规则",
                      icon: Network,
                      page: "ontology",
                      count: `${overview.entity_count} 类对象`,
                    },
                    {
                      title: "AI 业务能力",
                      sub: "REST API · MCP Tools",
                      icon: Boxes,
                      page: "capabilities",
                      count: `${capabilities.length} 项能力`,
                    },
                    {
                      title: "受控业务动作",
                      sub: "规则预检 · 人工审批",
                      icon: ShieldCheck,
                      page: "actions",
                      count: "仅创建草案",
                    },
                  ].map((s, i) => (
                    <React.Fragment key={s.title}>
                      <button
                        className={
                          "pipeline-step " + (i === 2 ? "featured" : "")
                        }
                        onClick={() => navigate(s.page as Page)}
                      >
                        <span className="pipeline-icon">
                          <s.icon size={23} />
                        </span>
                        <strong>{s.title}</strong>
                        <small>{s.sub}</small>
                        <span className="step-count">{s.count}</span>
                      </button>
                      {i < 4 && (
                        <div className="pipeline-arrow">
                          <span />
                          <ArrowRight size={14} />
                        </div>
                      )}
                    </React.Fragment>
                  ))}
                </div>
                <div className="pipeline-footer">
                  <ShieldCheck size={14} />
                  <span>全链路来源追溯</span>
                  <i />
                  确定性业务规则
                  <i />
                  审批与执行状态分离
                  <span className="footer-end">LOCAL-FIRST ARCHITECTURE</span>
                </div>
              </section>
              <div className="overview-bottom">
                <section className="panel order-panel">
                  <div className="panel-heading">
                    <div>
                      <h2>订单交付雷达</h2>
                      <span className="count">{overview.orders.length}</span>
                    </div>
                    <button
                      className="text-button"
                      onClick={() => {
                        navigate("capabilities");
                        setSelectedCapability("check_delivery_risk");
                      }}
                    >
                      风险诊断 <ArrowUpRight size={14} />
                    </button>
                  </div>
                  <div className="table-toolbar">
                    <div className="tabs">
                      {[
                        ["all", "全部订单"],
                        ["high", "高风险"],
                        ["unknown", "待核实"],
                      ].map(([v, t]) => (
                        <button
                          key={v}
                          className={riskFilter === v ? "selected" : ""}
                          onClick={() => setRiskFilter(v)}
                        >
                          {t}
                        </button>
                      ))}
                    </div>
                    <label className="search">
                      <Search size={15} />
                      <input
                        aria-label="搜索订单"
                        placeholder="搜索订单 / 客户"
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                      />
                    </label>
                  </div>
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>订单 / 客户</th>
                          <th>交付日期</th>
                          <th>生产进度</th>
                          <th>交付风险</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {filteredOrders.map((o: Json) => (
                          <tr key={o.id} onClick={() => void openOrder(o.id)}>
                            <td>
                              <strong className="mono">{o.id}</strong>
                              <small>
                                {o.customer} · {o.quantity.toLocaleString()} 件
                              </small>
                            </td>
                            <td>
                              {o.due_date.slice(5)}
                              <small>{o.product_id}</small>
                            </td>
                            <td>
                              <div className="progress-label">
                                {o.risk.progress}%
                              </div>
                              <div className="progress">
                                <span
                                  style={{ width: o.risk.progress + "%" }}
                                />
                              </div>
                            </td>
                            <td>
                              <Badge value={o.risk.level} />
                            </td>
                            <td>
                              <button
                                className="icon-button"
                                aria-label={"查看 " + o.id}
                              >
                                <ArrowUpRight size={16} />
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {filteredOrders.length === 0 && (
                      <Empty text="没有符合筛选条件的订单" />
                    )}
                  </div>
                  <div className="table-foot">
                    <span>依据 delivery_risk_v1 · 风险为规则估算</span>
                    <span>{filteredOrders.length} 条结果</span>
                  </div>
                </section>
                <div className="right-stack">
                  <section className="insight-card">
                    <div className="eyebrow">
                      BUSINESS INSIGHT <Activity size={16} />
                    </div>
                    <h2>
                      从“看见数据”
                      <br />
                      到“理解业务”
                    </h2>
                    <p>关联订单、工序与库存，解释交付风险背后的业务原因。</p>
                    <div className="insight-bars">
                      {[35, 55, 40, 78, 62, 92, 78, 100].map((h, i) => (
                        <span key={i} style={{ height: h + "%" }} />
                      ))}
                    </div>
                    <button
                      onClick={() => {
                        navigate("capabilities");
                        setSelectedCapability("get_order_360");
                      }}
                    >
                      查询订单全景 <ArrowRight size={16} />
                    </button>
                    <small>内置业务规则 · 无需 LLM 密钥</small>
                  </section>
                  <section className="panel activity-panel">
                    <div className="panel-heading">
                      <h2>最近同步</h2>
                      <Clock3 size={16} />
                    </div>
                    {overview.runs.length ? (
                      overview.runs.slice(0, 3).map((r: Json) => (
                        <div className="activity-item" key={r.id}>
                          <span className={"activity-icon " + r.status}>
                            {r.status === "success" ? (
                              <Check size={14} />
                            ) : (
                              <X size={14} />
                            )}
                          </span>
                          <div>
                            <strong>{r.source_name}</strong>
                            <small>
                              {r.status === "success"
                                ? `${r.records} 条记录 · ${r.changed} 条变更`
                                : "同步失败，检查运维记录"}
                            </small>
                          </div>
                          <time>{dateTime(r.at).split(" ")[1]}</time>
                        </div>
                      ))
                    ) : (
                      <Empty text="尚无同步记录" />
                    )}
                  </section>
                </div>
              </div>
            </>
          )}
          {page === "sources" && (
            <>
              <div className="info-strip">
                <ShieldCheck size={18} />
                <div>
                  <strong>本地网关 / 只读接入</strong>
                  <span>
                    文件路径相对于授权来源目录。单源上限 10,000 行，CSV 上限 10
                    MiB。同步使用完整快照。
                  </span>
                </div>
              </div>
              <div className="source-grid">
                {sources.map((s: Json) => (
                  <section className="panel source-card" key={s.id}>
                    <div className="source-card-top">
                      <span className={"source-icon " + s.system.toLowerCase()}>
                        {s.kind === "sqlite" ? (
                          <Database size={26} />
                        ) : (
                          <FileSpreadsheet size={26} />
                        )}
                      </span>
                      <Badge value={s.status} />
                    </div>
                    <span className="eyebrow">
                      {s.system} /{" "}
                      {s.kind === "sqlite"
                        ? "READ-ONLY DATABASE"
                        : "FILE CONNECTOR"}
                    </span>
                    <h2>{s.name}</h2>
                    <p className="mono source-path">{s.path}</p>
                    <div className="source-facts">
                      <div>
                        <small>同步对象</small>
                        <strong>
                          {s.records ?? "—"}
                          <span> 条</span>
                        </strong>
                      </div>
                      <div>
                        <small>来源时效 SLA</small>
                        <strong>
                          {s.freshness_minutes}
                          <span> 分钟</span>
                        </strong>
                      </div>
                    </div>
                    <p className="source-time">
                      <Clock3 size={13} />
                      {dateTime(s.synced_at)} {s.demo && "· 合成样例"}
                    </p>
                    {s.last_error && (
                      <p className="error-text">{s.last_error}</p>
                    )}
                    <footer>
                      <button
                        disabled={busy || !canEdit}
                        onClick={() =>
                          void run(async () => {
                            setDetail(await api(`/sources/${s.id}/schema`));
                            setDialog("schema");
                          })
                        }
                      >
                        <Layers3 size={15} />
                        发现结构
                      </button>
                      <button
                        className="primary subtle"
                        disabled={busy || !canEdit}
                        onClick={() => void sync(s.id)}
                      >
                        <RefreshCw size={15} />
                        立即同步
                      </button>
                    </footer>
                  </section>
                ))}
              </div>
              {!sources.length && (
                <Empty text="连接第一个数据库或 CSV 文件，开始建立业务本体" />
              )}
            </>
          )}
          {page === "mappings" && (
            <>
              <div className="info-strip">
                <GitBranch size={18} />
                <div>
                  <strong>来源字段 → 标准业务字段</strong>
                  <span>
                    预览真实记录，确认后同步。映射修改产生新版本，并使旧数据进入待核实状态。
                  </span>
                </div>
              </div>
              <section className="panel">
                <div className="panel-heading">
                  <h2>字段映射注册表</h2>
                  <span>{mappings.length} 个映射</span>
                </div>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>业务对象</th>
                        <th>来源 / 数据表</th>
                        <th>字段映射</th>
                        <th>版本</th>
                        <th>状态</th>
                        <th>操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {mappings.map((m: Json) => (
                        <tr key={m.id}>
                          <td>
                            <strong>
                              {
                                ontology.entities.find(
                                  (e: Json) => e.id === m.entity,
                                )?.label
                              }
                            </strong>
                            <small className="mono">{m.entity}</small>
                          </td>
                          <td>
                            {
                              sources.find((s: Json) => s.id === m.source_id)
                                ?.name
                            }
                            <small className="mono">{m.table}</small>
                          </td>
                          <td>{Object.keys(m.fields).length} 个字段</td>
                          <td className="mono">v{m.version}</td>
                          <td>
                            <Badge value={m.status} />
                          </td>
                          <td>
                            <div className="row-actions">
                              <button
                                disabled={!canEdit || busy}
                                onClick={() =>
                                  void run(async () => {
                                    setSchema(
                                      await api(
                                        `/sources/${m.source_id}/schema`,
                                      ),
                                    );
                                    setMapping({
                                      source_id: m.source_id,
                                      table: m.table,
                                      entity: m.entity,
                                      fields: m.fields,
                                    });
                                    setPreview(null);
                                    setDialog("mapping");
                                  })
                                }
                              >
                                编辑
                              </button>
                              {m.status === "draft" && (
                                <button
                                  disabled={!canEdit || busy}
                                  onClick={() =>
                                    void run(async () => {
                                      await api(
                                        `/mappings/${m.id}/confirm`,
                                        {},
                                      );
                                      await refresh();
                                      setToast("映射已确认，可执行数据同步");
                                    })
                                  }
                                >
                                  确认
                                </button>
                              )}
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
          {page === "ontology" && (
            <>
              <div className="ontology-layout">
                <section className="panel graph-panel">
                  <div className="panel-heading">
                    <div>
                      <h2>制造业业务本体</h2>
                      <span className="muted">7 类对象 · 6 类关系</span>
                    </div>
                    <span className="badge confirmed">
                      <i />
                      来源引用
                    </span>
                  </div>
                  <div className="graph">
                    <ReactFlow
                      nodes={nodes}
                      edges={edges}
                      fitView
                      onNodeClick={(_, node) => setSelectedEntity(node.id)}
                      nodesDraggable={false}
                      proOptions={{ hideAttribution: true }}
                    >
                      <Background gap={22} color="#dce4df" />
                      <Controls showInteractive={false} />
                    </ReactFlow>
                  </div>
                  <div className="table-foot">
                    点击对象查看字段和数据血缘；关系由确定性引用建立。
                  </div>
                </section>
                <section className="panel entity-panel">
                  <span className="eyebrow">ENTITY DEFINITION</span>
                  <h2>{entity.label}</h2>
                  <code>{entity.id}</code>
                  <p>{entity.count} 个实例 · 主键 id</p>
                  <div className="field-list">
                    {Object.entries(entity.schema.properties).map(
                      ([key, value]: [string, Json]) => (
                        <div key={key}>
                          <span className="mono">{key}</span>
                          <small>{value.format || value.type}</small>
                        </div>
                      ),
                    )}
                  </div>
                  <button
                    onClick={() => download(entity, entity.id + ".schema.json")}
                  >
                    <ArrowDownToLine size={15} />
                    导出对象定义
                  </button>
                </section>
              </div>
              <section className="panel">
                <div className="panel-heading">
                  <h2>{entity.label} · 数据实例</h2>
                  <span>{entity.count} 条</span>
                </div>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>业务主键</th>
                        <th>Canonical ID</th>
                        <th>来源</th>
                        <th>数据版本</th>
                        <th>血缘</th>
                      </tr>
                    </thead>
                    <tbody>
                      {ontology.objects
                        .filter((o: Json) => o.entity === selectedEntity)
                        .map((o: Json) => (
                          <tr key={o.id}>
                            <td className="mono">{o.data.id}</td>
                            <td className="mono muted">{o.id}</td>
                            <td>
                              {o.source_name}
                              <small>{o.source_table}</small>
                            </td>
                            <td>v{o.version}</td>
                            <td>
                              <button
                                onClick={() =>
                                  void run(async () => {
                                    setDetail(
                                      await api(`/objects/${o.id}/history`),
                                    );
                                    setDialog("history");
                                  })
                                }
                              >
                                查看版本
                              </button>
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </section>
              <section className="panel review-panel">
                <div className="panel-heading">
                  <h2>身份关联复核</h2>
                  <span className="count">{ontology.issues.length}</span>
                </div>
                {ontology.issues.length ? (
                  ontology.issues.map((i: Json) => (
                    <div className="review-item" key={i.id}>
                      <div>
                        <strong>
                          {i.source_key} → {i.value}
                        </strong>
                        <p>
                          {i.label} · {i.reason}
                        </p>
                      </div>
                      <button
                        disabled={!canEdit}
                        onClick={() => {
                          setResolution({
                            ...i,
                            target_id: i.candidates[0]?.id || "",
                            reason: "",
                          });
                          setDialog("resolution");
                        }}
                      >
                        人工复核
                      </button>
                    </div>
                  ))
                ) : (
                  <div className="all-clear">
                    <CheckCircle2 size={20} />
                    所有来源引用均已关联，没有待复核记录。
                  </div>
                )}
              </section>
            </>
          )}
          {page === "capabilities" && (
            <div className="capability-layout">
              <section className="panel capability-list">
                <div className="panel-heading">
                  <h2>业务能力注册表</h2>
                  <span className="count">7</span>
                </div>
                {capabilities.map((c: Json) => (
                  <button
                    key={c.name}
                    className={selectedCapability === c.name ? "selected" : ""}
                    onClick={() => {
                      setSelectedCapability(c.name);
                      setResult(null);
                    }}
                  >
                    <div>
                      <span className="cap-icon">
                        {c.write ? <Workflow size={18} /> : <Code2 size={18} />}
                      </span>
                      <strong>{c.label}</strong>
                      <span className={"tiny-tag " + (c.write ? "amber" : "")}>
                        {c.write ? "草案" : "只读"}
                      </span>
                    </div>
                    <code>{c.name}</code>
                    <p>{c.description}</p>
                  </button>
                ))}
              </section>
              <div className="capability-workbench">
                <section className="panel">
                  <div className="panel-heading">
                    <div>
                      <h2>能力调试台</h2>
                      <span className="tiny-tag">REST / MCP</span>
                    </div>
                    <a href="/api/docs" target="_blank" rel="noreferrer">
                      API 文档 ↗
                    </a>
                  </div>
                  <div className="workbench-form">
                    <span className="eyebrow">{selectedCapability}</span>
                    <h2>
                      {
                        capabilities.find(
                          (c: Json) => c.name === selectedCapability,
                        )?.label
                      }
                    </h2>
                    <p>
                      {
                        capabilities.find(
                          (c: Json) => c.name === selectedCapability,
                        )?.description
                      }
                    </p>
                    <div className="form-grid">
                      {!["list_due_orders", "simulate_schedule"].includes(
                        selectedCapability,
                      ) && (
                        <label>
                          订单号
                          <select
                            value={args.order_id}
                            onChange={(e) =>
                              setArgs({ ...args, order_id: e.target.value })
                            }
                          >
                            {overview.orders.map((o: Json) => (
                              <option key={o.id} value={o.id}>
                                {o.id} · {o.customer}
                              </option>
                            ))}
                          </select>
                        </label>
                      )}
                      {selectedCapability === "list_due_orders" && (
                        <label>
                          未来天数
                          <input
                            type="number"
                            min="0"
                            max="365"
                            value={args.days}
                            onChange={(e) =>
                              setArgs({ ...args, days: Number(e.target.value) })
                            }
                          />
                        </label>
                      )}
                      {[
                        "simulate_schedule",
                        "propose_schedule_change",
                      ].includes(selectedCapability) && (
                        <label>
                          每日追加工时（0–8 小时）
                          <input
                            type="number"
                            min="0"
                            max="8"
                            value={args.extra_hours}
                            onChange={(e) =>
                              setArgs({
                                ...args,
                                extra_hours: Number(e.target.value),
                              })
                            }
                          />
                        </label>
                      )}
                    </div>
                    {selectedCapability === "propose_schedule_change" && (
                      <label>
                        业务说明
                        <textarea
                          value={args.note}
                          onChange={(e) =>
                            setArgs({ ...args, note: e.target.value })
                          }
                        />
                      </label>
                    )}
                    <div className="workbench-actions">
                      <span>
                        <ShieldCheck size={14} />
                        {selectedCapability === "propose_schedule_change"
                          ? "需独立审批人审核 · 无生产写回"
                          : "读取标准对象 · 返回来源血缘"}
                      </span>
                      <button
                        className="primary"
                        disabled={
                          busy ||
                          (selectedCapability === "propose_schedule_change" &&
                            !canEdit)
                        }
                        onClick={() => void invoke()}
                      >
                        <Play size={15} />
                        {selectedCapability === "propose_schedule_change"
                          ? "提交草案"
                          : "运行能力"}
                      </button>
                    </div>
                  </div>
                </section>
                <section className="panel result-panel">
                  <div className="panel-heading">
                    <h2>业务结果</h2>
                    {result && (
                      <button
                        className="text-button"
                        onClick={() =>
                          download(result, selectedCapability + ".json")
                        }
                      >
                        <ArrowDownToLine size={15} />
                        导出
                      </button>
                    )}
                  </div>
                  {result ? (
                    <>
                      <div className="result-meta">
                        <CheckCircle2 size={17} />
                        {dateTime(result.meta.computed_at)}
                        <span>{result.meta.rule_version}</span>
                        <span>{result.meta.provenance.length} 条来源记录</span>
                      </div>
                      {result.result.level && (
                        <div className="risk-summary">
                          <Badge value={result.result.level} />
                          <p>{result.result.reasons.join("；")}</p>
                        </div>
                      )}
                      {result.result.status && (
                        <div className="risk-summary">
                          <Badge value={result.result.status} />
                          <p>{result.result.id} · 执行状态：未执行</p>
                        </div>
                      )}
                      <JsonView value={result} />
                    </>
                  ) : (
                    <Empty text="选择业务能力并运行，查看真实计算结果" />
                  )}
                </section>
                <details className="panel mcp-guide">
                  <summary>
                    <Terminal size={16} />
                    连接你的 AI Agent
                  </summary>
                  <p>
                    启动 API 后，使用同一权限令牌运行官方 MCP SDK stdio 服务。
                  </p>
                  <JsonView
                    value={{
                      mcpServers: {
                        ontobridge: {
                          command: "uv",
                          args: [
                            "--directory",
                            "/Volumes/ExtSSD/Github/ontobridge",
                            "run",
                            "ontobridge-mcp",
                          ],
                          env: {
                            ONTOBRIDGE_API_URL: "http://127.0.0.1:8787",
                            ONTOBRIDGE_API_TOKEN: "<企业配置的访问令牌>",
                          },
                        },
                      },
                    }}
                  />
                  <button
                    onClick={() => {
                      setDetail(
                        capabilities.find(
                          (c: Json) => c.name === selectedCapability,
                        ),
                      );
                      setDialog("contract");
                    }}
                  >
                    查看能力契约
                  </button>
                </details>
              </div>
            </div>
          )}
          {page === "actions" && (
            <>
              <div className="info-strip">
                <ShieldCheck size={18} />
                <div>
                  <strong>审批通过 ≠ 源系统已执行</strong>
                  <span>
                    当前版本只支持低风险草案。申请人与审批人必须分离，审批时重新核验输入数据版本与时效。
                  </span>
                </div>
              </div>
              <div className="action-flow">
                {["业务建议", "规则预检", "人工审批", "已批准草案"].map(
                  (s, i) => (
                    <React.Fragment key={s}>
                      <span>
                        <b>0{i + 1}</b>
                        {s}
                      </span>
                      {i < 3 && <ArrowRight size={17} />}
                    </React.Fragment>
                  ),
                )}
                <span className="disabled-step">生产执行 · 未启用</span>
              </div>
              <section className="panel">
                <div className="panel-heading">
                  <h2>业务动作队列</h2>
                  <button
                    className="text-button"
                    onClick={() => {
                      navigate("capabilities");
                      setSelectedCapability("propose_schedule_change");
                    }}
                  >
                    创建草案 <Plus size={15} />
                  </button>
                </div>
                {actions.length ? (
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>草案 / 订单</th>
                          <th>业务说明</th>
                          <th>申请人</th>
                          <th>状态</th>
                          <th>创建时间</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {actions.map((a: Json) => (
                          <tr key={a.id}>
                            <td>
                              <strong className="mono">{a.id}</strong>
                              <small>{a.order_id}</small>
                            </td>
                            <td className="note-cell">
                              {a.note}
                              <small>
                                每日追加 {a.extra_hours} 小时 · 未执行
                              </small>
                            </td>
                            <td>{a.created_by}</td>
                            <td>
                              <Badge value={a.status} />
                            </td>
                            <td>{dateTime(a.created_at)}</td>
                            <td>
                              <button
                                onClick={() => {
                                  setSelectedAction(a);
                                  setReason("");
                                  setDialog("action");
                                }}
                              >
                                查看 / 审批
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <Empty text="暂无动作草案。可在能力调试台提交排产调整建议。" />
                )}
              </section>
            </>
          )}
          {page === "operations" && (
            <>
              <div className="ops-status">
                <section className="panel">
                  <span className="pulse" />
                  <div>
                    <h2>本地连接网关</h2>
                    <p>Local Gateway · 单进程 · 持久化存储</p>
                  </div>
                  <Badge value="connected" />
                </section>
                <section className="panel">
                  <ShieldCheck size={27} />
                  <div>
                    <h2>生产写回关闭</h2>
                    <p>只读查询与审批草案 · 完整快照同步</p>
                  </div>
                </section>
              </div>
              <section className="panel">
                <div className="panel-heading">
                  <h2>同步运行记录</h2>
                  <span>最近 100 批次</span>
                </div>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>来源</th>
                        <th>执行时间</th>
                        <th>结果</th>
                        <th>记录 / 变更 / 移除</th>
                        <th>详情</th>
                      </tr>
                    </thead>
                    <tbody>
                      {operations.runs.map((r: Json) => (
                        <tr key={r.id}>
                          <td>{r.source_name}</td>
                          <td>{dateTime(r.at)}</td>
                          <td>
                            <Badge value={r.status} />
                          </td>
                          <td>
                            {r.records} / {r.changed ?? "—"} /{" "}
                            {r.removed ?? "—"}
                          </td>
                          <td className="note-cell">
                            {r.error || "事务提交完成 · 可重复同步"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
              <section className="panel audit-panel">
                <div className="panel-heading">
                  <h2>审计日志</h2>
                  <span>最近 200 条 · 当前租户</span>
                </div>
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>时间</th>
                        <th>操作者</th>
                        <th>事件</th>
                        <th>目标</th>
                        <th>详情</th>
                      </tr>
                    </thead>
                    <tbody>
                      {operations.audit.map((a: Json) => (
                        <tr key={a.id}>
                          <td>{dateTime(a.at)}</td>
                          <td>{a.actor}</td>
                          <td className="mono">{a.event}</td>
                          <td className="mono">{a.target}</td>
                          <td>
                            <button
                              onClick={() => {
                                setDetail(a);
                                setDialog("audit");
                              }}
                            >
                              查看
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
          <footer className="page-footer">
            <span>
              <span className="green-dot" />
              OntoBridge · Enterprise AI Integration Platform
            </span>
            <span>
              {config.demo ? "所有业务记录均为合成演示数据" : "企业授权数据"}{" "}
              <i /> v0.1.0
            </span>
          </footer>
        </div>
      </main>
      {toast && (
        <div className="toast" role="status">
          <CheckCircle2 size={18} />
          {toast}
        </div>
      )}
      {busy && <div className="busy-bar" />}
      {dialog === "source" && (
        <Modal title="连接本地数据源" close={() => setDialog("")}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                await api("/sources", sourceForm);
                await refresh();
                setDialog("");
                setSourceForm({
                  name: "",
                  system: "ERP",
                  kind: "sqlite",
                  path: "",
                  freshness_minutes: 1440,
                });
                setToast("数据源已创建，请配置字段映射");
              });
            }}
          >
            <div className="modal-body">
              <label>
                数据源名称
                <input
                  value={sourceForm.name}
                  onChange={(e) =>
                    setSourceForm({ ...sourceForm, name: e.target.value })
                  }
                  placeholder="例如：工厂 ERP 订单库"
                  required
                />
              </label>
              <div className="form-grid">
                <label>
                  来源系统
                  <select
                    value={sourceForm.system}
                    onChange={(e) =>
                      setSourceForm({ ...sourceForm, system: e.target.value })
                    }
                  >
                    {["ERP", "MES", "WMS", "OTHER"].map((s) => (
                      <option key={s}>{s}</option>
                    ))}
                  </select>
                </label>
                <label>
                  连接方式
                  <select
                    value={sourceForm.kind}
                    onChange={(e) =>
                      setSourceForm({ ...sourceForm, kind: e.target.value })
                    }
                  >
                    <option value="sqlite">SQLite 只读数据库</option>
                    <option value="csv">CSV 文件导入</option>
                  </select>
                </label>
              </div>
              <label>
                授权目录内的文件路径
                <input
                  required
                  placeholder="exports/orders.sqlite 或 exports/orders.csv"
                  value={sourceForm.path}
                  onChange={(e) =>
                    setSourceForm({ ...sourceForm, path: e.target.value })
                  }
                />
              </label>
              <label>
                来源时效 SLA（分钟）
                <input
                  type="number"
                  min="1"
                  max="525600"
                  value={sourceForm.freshness_minutes}
                  onChange={(e) =>
                    setSourceForm({
                      ...sourceForm,
                      freshness_minutes: Number(e.target.value),
                    })
                  }
                />
              </label>
              <p className="muted">
                默认授权目录为 var/sources。提交时将执行真实的只读结构发现。
              </p>
              {error && <div className="alert">{error}</div>}
            </div>
            <footer>
              <button type="button" onClick={() => setDialog("")}>
                取消
              </button>
              <button className="primary" disabled={busy}>
                验证并连接 <ArrowRight size={16} />
              </button>
            </footer>
          </form>
        </Modal>
      )}
      {dialog === "mapping" && (
        <Modal title="业务字段映射" close={() => setDialog("")} wide>
          <div className="modal-body">
            <div className="form-grid triple">
              <label>
                数据源
                <select
                  value={mapping.source_id}
                  onChange={(e) => void chooseSource(e.target.value)}
                >
                  <option value="">选择数据源</option>
                  {sources.map((s: Json) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                来源表
                <select
                  value={mapping.table}
                  onChange={(e) => {
                    setMapping({
                      ...mapping,
                      table: e.target.value,
                      fields: {},
                    });
                    setPreview(null);
                  }}
                >
                  <option value="">选择来源表</option>
                  {schema.map((t) => (
                    <option key={t.name}>{t.name}</option>
                  ))}
                </select>
              </label>
              <label>
                业务对象
                <select
                  value={mapping.entity}
                  onChange={(e) => {
                    setMapping({
                      ...mapping,
                      entity: e.target.value,
                      fields: {},
                    });
                    setPreview(null);
                  }}
                >
                  {ontology.entities.map((e: Json) => (
                    <option key={e.id} value={e.id}>
                      {e.label} / {e.id}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="mapping-fields">
              {Object.keys(
                ontology.entities.find((e: Json) => e.id === mapping.entity)
                  .schema.properties,
              ).map((key) => (
                <div key={key}>
                  <code>{key}</code>
                  <ArrowRight size={16} />
                  <select
                    aria-label={"来源字段 " + key}
                    value={mapping.fields[key] || ""}
                    onChange={(e) => {
                      setMapping({
                        ...mapping,
                        fields: { ...mapping.fields, [key]: e.target.value },
                      });
                      setPreview(null);
                    }}
                  >
                    <option value="">选择来源字段</option>
                    {(
                      schema.find((t) => t.name === mapping.table)?.columns ||
                      []
                    ).map((c: string) => (
                      <option key={c}>{c}</option>
                    ))}
                  </select>
                </div>
              ))}
            </div>
            {error && <div className="alert">{error}</div>}
            {preview && (
              <>
                <p
                  className={
                    preview.error_count ? "error-text" : "success-text"
                  }
                >
                  {preview.valid_count} 行有效 · {preview.error_count} 行错误
                </p>
                <JsonView
                  value={preview.error_count ? preview.errors : preview.rows}
                />
              </>
            )}
          </div>
          <footer>
            <button
              disabled={busy}
              onClick={() =>
                void run(async () =>
                  setPreview(await api("/mappings/preview", mapping)),
                )
              }
            >
              <Play size={15} />
              校验并预览
            </button>
            <button
              className="primary"
              disabled={busy || !preview || preview.error_count > 0}
              onClick={() =>
                void run(async () => {
                  await api("/mappings", mapping);
                  await refresh();
                  setDialog("");
                  setToast("已保存映射草案，请确认后再同步");
                })
              }
            >
              保存待确认映射
            </button>
          </footer>
        </Modal>
      )}
      {dialog === "order" && detail && (
        <Modal title="订单业务全景" close={() => setDialog("")} wide>
          <div className="modal-body">
            <div className="order-detail-head">
              <div>
                <code>{detail.result.order.data.id}</code>
                <h2>{detail.result.order.data.customer}</h2>
                <p>
                  {detail.result.order.data.product_id} ·{" "}
                  {detail.result.order.data.quantity} 件 ·{" "}
                  {detail.result.order.data.due_date} 交付
                </p>
              </div>
              <Badge value={detail.result.risk.level} />
            </div>
            <div className="risk-summary">
              <Activity size={19} />
              <div>
                <strong>交期风险 · delivery_risk_v1</strong>
                <p>{detail.result.risk.reasons.join("；")}</p>
              </div>
            </div>
            <div className="detail-stats">
              <div>
                <small>生产完成</small>
                <strong>{detail.result.progress.percent}%</strong>
              </div>
              <div>
                <small>关联工单</small>
                <strong>{detail.result.progress.work_orders.length}</strong>
              </div>
              <div>
                <small>预计生产天数</small>
                <strong>{detail.result.risk.production_days ?? "未知"}</strong>
              </div>
              <div>
                <small>物料缺口</small>
                <strong>
                  {detail.result.risk.material_shortage ?? "未知"}
                </strong>
              </div>
            </div>
            <h3>关联工单与来源</h3>
            {detail.result.progress.work_orders.map((w: Json) => (
              <div className="review-item" key={w.id}>
                <div>
                  <strong className="mono">{w.data.id}</strong>
                  <p>
                    {w.source_name} / {w.source_table} · 来源主键 {w.source_key}
                  </p>
                </div>
                <span>
                  {w.data.finished} / {w.data.quantity} 件
                </span>
              </div>
            ))}
            <details>
              <summary>计算假设与完整数据血缘</summary>
              <JsonView value={detail} />
            </details>
          </div>
          <footer>
            <button
              onClick={() =>
                download(detail, detail.result.order.data.id + ".json")
              }
            >
              <ArrowDownToLine size={16} />
              导出全景
            </button>
            <button
              className="primary"
              disabled={!canEdit}
              onClick={() => {
                setArgs({ ...args, order_id: detail.result.order.data.id });
                setSelectedCapability("propose_schedule_change");
                navigate("capabilities");
                setDialog("");
              }}
            >
              创建调整草案 <ArrowRight size={16} />
            </button>
          </footer>
        </Modal>
      )}
      {dialog === "action" && selectedAction && (
        <Modal title="业务草案审批" close={() => setDialog("")} wide>
          <div className="modal-body">
            <div className="order-detail-head">
              <div>
                <code>{selectedAction.id}</code>
                <h2>{selectedAction.order_id}</h2>
                <p>{selectedAction.note}</p>
              </div>
              <Badge value={selectedAction.status} />
            </div>
            <div className="warning">
              <ShieldCheck size={18} />
              批准后仅保存为“已批准草案”，不会向 MES 提交。
            </div>
            <div className="form-grid">
              <div>
                <h3>调整前</h3>
                <p>{selectedAction.precheck.reasons.join("；")}</p>
              </div>
              <div>
                <h3>每日追加 {selectedAction.extra_hours} 小时</h3>
                <p>{selectedAction.scenario.reasons.join("；")}</p>
              </div>
            </div>
            <h3>状态历史</h3>
            {selectedAction.history.map((h: Json, i: number) => (
              <div className="review-item" key={i}>
                <Badge value={h.status} />
                <span>
                  {h.actor} · {dateTime(h.at)}
                </span>
                <p>{h.reason}</p>
              </div>
            ))}
            {selectedAction.status === "pending_approval" && (
              <label>
                审批说明
                <textarea
                  placeholder="请输入至少 3 个字符的审批依据"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
              </label>
            )}
            {!canApprove && (
              <p className="muted">
                需要 PMC 审批人角色；演示空间可在左下角切换角色。
              </p>
            )}
            {error && <div className="alert">{error}</div>}
          </div>
          <footer>
            <button
              onClick={() =>
                download(selectedAction, selectedAction.id + ".json")
              }
            >
              <ArrowDownToLine size={15} />
              导出草案
            </button>
            {selectedAction.status === "pending_approval" &&
              (["reject", "approve"] as const).map((d) => (
                <button
                  key={d}
                  className={d === "approve" ? "primary" : ""}
                  disabled={
                    busy ||
                    !canApprove ||
                    reason.trim().length < 3 ||
                    selectedAction.created_by === principal.user
                  }
                  onClick={() =>
                    void run(async () => {
                      setSelectedAction(
                        await api(`/actions/${selectedAction.id}/decision`, {
                          decision: d,
                          reason,
                        }),
                      );
                      await refresh();
                      setToast(
                        d === "approve"
                          ? "草案审批通过，未执行生产操作"
                          : "草案已驳回",
                      );
                    })
                  }
                >
                  {d === "approve" ? "批准草案" : "驳回"}
                </button>
              ))}
          </footer>
        </Modal>
      )}
      {dialog === "resolution" && resolution && (
        <Modal title="人工确认业务关联" close={() => setDialog("")}>
          <div className="modal-body">
            <p>
              {resolution.source_key} 的 {resolution.field} 引用{" "}
              {resolution.value} 未找到。
            </p>
            <label>
              确认目标业务对象
              <select
                value={resolution.target_id}
                onChange={(e) =>
                  setResolution({ ...resolution, target_id: e.target.value })
                }
              >
                {resolution.candidates.map((c: Json) => (
                  <option key={c.id} value={c.id}>
                    {c.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              关联依据
              <textarea
                value={resolution.reason}
                onChange={(e) =>
                  setResolution({ ...resolution, reason: e.target.value })
                }
                placeholder="请填写单据或业务确认依据"
              />
            </label>
            {error && <div className="alert">{error}</div>}
          </div>
          <footer>
            <button
              className="primary"
              disabled={
                busy ||
                !resolution.target_id ||
                resolution.reason.trim().length < 3
              }
              onClick={() =>
                void run(async () => {
                  await api(`/resolutions/${resolution.id}`, {
                    target_id: resolution.target_id,
                    reason: resolution.reason,
                  });
                  await refresh();
                  setDialog("");
                  setToast("关联已确认，已保留人工复核依据");
                })
              }
            >
              确认关联
            </button>
          </footer>
        </Modal>
      )}
      {["schema", "history", "help", "audit", "contract"].includes(dialog) && (
        <Modal
          title={
            {
              schema: "来源结构发现",
              history: "数据血缘与版本",
              help: "关于 OntoBridge",
              audit: "审计事件详情",
              contract: "业务能力契约",
            }[dialog]!
          }
          close={() => setDialog("")}
          wide
        >
          <div className="modal-body">
            <JsonView value={detail} />
          </div>
          <footer>
            <button
              onClick={() => download(detail, "ontobridge-" + dialog + ".json")}
            >
              <ArrowDownToLine size={15} />
              导出 JSON
            </button>
            <button className="primary" onClick={() => setDialog("")}>
              完成
            </button>
          </footer>
        </Modal>
      )}
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
