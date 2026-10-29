import { useEffect, useState } from "react";
import {
  Boxes,
  Plus,
  Play,
  ShieldCheck,
  Code2,
  TestTube2,
  History,
  Activity,
  RefreshCw,
  ArrowRight,
} from "lucide-react";
import StudioModal from "./StudioModal";
type J = any;
type API = (path: string, body?: J) => Promise<J>;
const stamp = (s?: string) => (s ? new Date(s).toLocaleString("zh-CN") : "—");
export default function CapabilityFactory({
  api,
  principal,
}: {
  api: API;
  principal: J;
}) {
  const edit =
      !principal.capability_only &&
      ["operator", "admin"].includes(principal.role),
    approve =
      !principal.capability_only &&
      ["approver", "admin"].includes(principal.role);
  const [records, setRecords] = useState<J[]>([]),
    [templates, setTemplates] = useState<J[]>([]),
    [ontologies, setOntologies] = useState<J[]>([]),
    [published, setPublished] = useState<J[]>([]),
    [versions, setVersions] = useState<J[]>([]),
    [tools, setTools] = useState<J[]>([]),
    [operations, setOperations] = useState<J>(null),
    [actions, setActions] = useState<J[]>([]);
  const [selected, setSelected] = useState(""),
    [tab, setTab] = useState("design"),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [modal, setModal] = useState("");
  const [form, setForm] = useState<J>(null),
    [argumentsText, setArguments] = useState("{}"),
    [result, setResult] = useState<J>(null),
    [report, setReport] = useState<J>(null),
    [detail, setDetail] = useState<J>(null),
    [reason, setReason] = useState("");
  const current = records.find((c) => c.name === selected),
    generated = versions.find((v) => v.id === current?.generated_id),
    active = versions.find((v) => v.version === current?.active_version);
  async function load(name = selected) {
    const [rs, ts] = await Promise.all([
      api("/v1/capabilities"),
      api("/v1/tools"),
    ]);
    setRecords(rs);
    setTools(ts.tools);
    if (edit || approve) {
      const [tm, os, ops, as] = await Promise.all([
        api("/v1/capability-templates"),
        api("/v1/ontologies"),
        api("/v1/capability-operations"),
        api("/v1/actions"),
      ]);
      setTemplates(tm);
      setOntologies(os);
      setOperations(ops);
      setActions(as);
      const key = name || rs[0]?.name || "";
      setSelected(key);
      setVersions(key ? await api(`/v1/capabilities/${key}/versions`) : []);
    } else setSelected(name || ts.tools[0]?.name || "");
  }
  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, [principal.user]);
  async function action(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function choose(name: string) {
    setSelected(name);
    setResult(null);
    setReport(null);
    if (edit || approve) {
      const vs = await api(`/v1/capabilities/${name}/versions`);
      setVersions(vs);
      setArguments(
        JSON.stringify(
          vs.find(
            (v: J) =>
              v.id === records.find((r) => r.name === name)?.generated_id,
          )?.example || {},
          null,
          2,
        ),
      );
    } else {
      const tool = tools.find((t) => t.name === name);
      setArguments(
        JSON.stringify(
          Object.fromEntries(
            Object.entries(tool?.inputSchema.properties || {}).map(([k, v]) => [
              k,
              (v as J).default ?? ((v as J).type === "string" ? "" : 0),
            ]),
          ),
          null,
          2,
        ),
      );
    }
  }
  async function openForm() {
    const ontology = ontologies.find((o) => o.active_version);
    if (!ontology)
      throw new Error("请先在本体工作台发布一个通过校验的本体版本。");
    const vs = await api(`/v1/ontologies/${ontology.id}/versions`);
    setPublished(vs);
    setForm({
      name: "get_order_360",
      title: "订单全景查询",
      description: "查询订单、工单、生产进度、交期风险与原始数据来源。",
      ontology_id: ontology.id,
      ontology_version: ontology.active_version,
      template: "order_360",
      root_object: "SalesOrder",
      roles: ["viewer", "operator", "approver", "admin"],
      max_results: 100,
      timeout_seconds: 10,
      rule_version: "manufacturing_v1",
    });
    setModal("create");
  }
  return (
    <div className="v1-studio factory-studio">
      <div className="studio-toolbar">
        <div className="studio-actions">
          <select
            aria-label="选择业务能力"
            value={selected}
            onChange={(e) => action(() => choose(e.target.value))}
          >
            <option value="">选择能力</option>
            {records.map((c) => (
              <option key={c.name} value={c.name}>
                {c.spec?.title || c.title} · {c.name}
              </option>
            ))}
          </select>
          {current?.active_version && (
            <span className="badge">已发布 v{current.active_version}</span>
          )}
        </div>
        <div className="studio-actions">
          <button
            aria-label="刷新能力"
            disabled={busy}
            onClick={() => action(() => load())}
          >
            <RefreshCw size={16} />
          </button>
          {edit && (
            <button
              className="primary"
              disabled={busy}
              onClick={() => action(openForm)}
            >
              <Plus size={16} />
              配置业务能力
            </button>
          )}
        </div>
      </div>
      {error && (
        <div className="error-banner" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className="studio-notice" role="status">
          {notice}
        </div>
      )}
      {(edit || approve) && (
        <div className="tabs">
          {[
            ["design", "能力定义"],
            ["sandbox", "沙箱与调用"],
            ["versions", "发布治理"],
            ["operations", "调用监控"],
            ["actions", "动作草案"],
          ].map(([id, label]) => (
            <button
              key={id}
              className={tab === id ? "active" : ""}
              onClick={() => setTab(id)}
            >
              {label}
            </button>
          ))}
        </div>
      )}
      {!records.length && (
        <div className="empty-state">
          <Boxes size={38} />
          <h3>将已发布本体封装成业务能力</h3>
          <p>
            选择经过验证的查询模板，生成 REST 与 MCP 定义，完成沙箱测试后发布。
          </p>
        </div>
      )}
      {current && (edit || approve) && tab === "design" && (
        <>
          <div className="factory-hero">
            <div>
              <span className="eyebrow">
                CAPABILITY CONTRACT / r{current.revision}
              </span>
              <h2>{current.spec.title}</h2>
              <code>{current.name}</code>
              <p>{current.spec.description}</p>
            </div>
            <div className="factory-flow">
              <span>本体 v{current.spec.ontology_version}</span>
              <ArrowRight size={15} />
              <span>{current.spec.template}</span>
              <ArrowRight size={15} />
              <span>REST / MCP</span>
            </div>
          </div>
          <div className="two-col">
            <section className="panel">
              <div className="panel-heading">
                <h2>执行与授权</h2>
              </div>
              <dl className="factory-facts">
                <dt>批准的模板</dt>
                <dd>
                  {
                    templates.find((t) => t.id === current.spec.template)
                      ?.description
                  }
                </dd>
                <dt>本体</dt>
                <dd>
                  {
                    ontologies.find((o) => o.id === current.spec.ontology_id)
                      ?.spec.name
                  }{" "}
                  · v{current.spec.ontology_version}
                </dd>
                <dt>业务规则</dt>
                <dd>{current.spec.rule_version}</dd>
                <dt>可调用角色</dt>
                <dd>{current.spec.roles.join(" / ")}</dd>
                <dt>结果与耗时上限</dt>
                <dd>
                  {current.spec.max_results} 条 · {current.spec.timeout_seconds}{" "}
                  秒
                </dd>
              </dl>
              <div className="studio-actions panel-buttons">
                {edit && (
                  <button
                    className="primary"
                    disabled={busy}
                    onClick={() =>
                      action(async () => {
                        const result = await api(
                          `/v1/capabilities/${selected}/generate`,
                          {},
                        );
                        await load();
                        setArguments(JSON.stringify(result.example, null, 2));
                        setTab("sandbox");
                        setNotice("已生成参数 Schema、工具定义和沙箱用例。");
                      })
                    }
                  >
                    <Code2 size={16} />
                    生成 API / MCP
                  </button>
                )}
                {edit && (
                  <button
                    onClick={() =>
                      action(async () => {
                        setForm(structuredClone(current.spec));
                        setPublished(
                          await api(
                            `/v1/ontologies/${current.spec.ontology_id}/versions`,
                          ),
                        );
                        setModal("create");
                      })
                    }
                  >
                    修订草案
                  </button>
                )}
              </div>
            </section>
            <section className="panel">
              <div className="panel-heading">
                <h2>接口契约</h2>
              </div>
              <p>
                <code>POST /api/v1/invoke/{selected}</code>
              </p>
              <p>
                只有发布版本可从 REST 和 MCP
                调用。每次调用均检查角色、数据权限、来源时效与本体质量。
              </p>
              {generated ? (
                <pre className="contract-preview">
                  {JSON.stringify(
                    {
                      input: generated.input_schema,
                      output: generated.output_schema,
                    },
                    null,
                    2,
                  )}
                </pre>
              ) : (
                <div className="empty-state">
                  生成后将在此显示参数和结果 Schema。
                </div>
              )}
            </section>
          </div>
        </>
      )}
      {current && (edit || approve ? tab === "sandbox" : true) && (
        <div className="two-col">
          <section className="panel">
            <div className="panel-heading">
              <h2>输入参数</h2>
              <span>
                {generated ? `待验证 v${generated.version}` : "已发布能力"}
              </span>
            </div>
            <div className="sandbox-form">
              <label>
                JSON 参数
                <textarea
                  className="dsl-editor"
                  value={argumentsText}
                  onChange={(e) => setArguments(e.target.value)}
                />
              </label>
              <div className="studio-actions">
                {(edit || approve) && (
                  <>
                    <button
                      disabled={busy || !generated}
                      onClick={() =>
                        action(async () =>
                          setResult(
                            await api(`/v1/capabilities/${selected}/preview`, {
                              arguments: JSON.parse(argumentsText),
                            }),
                          ),
                        )
                      }
                    >
                      <Play size={15} />
                      沙箱预览
                    </button>
                    <button
                      className="primary"
                      disabled={busy || !generated}
                      onClick={() =>
                        action(async () => {
                          const r = await api(
                            `/v1/capabilities/${selected}/test`,
                            {},
                          );
                          setReport(r);
                          setResult(r.preview);
                          await load();
                          setNotice(
                            r.passed
                              ? "生成测试通过，可提交发布审核。"
                              : "测试失败，请修正依赖后重新测试。",
                          );
                        })
                      }
                    >
                      <TestTube2 size={15} />
                      运行生成测试
                    </button>
                  </>
                )}
                {tools.some((t) => t.name === selected) && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      action(async () => {
                        setResult(
                          await api(
                            `/v1/invoke/${selected}`,
                            JSON.parse(argumentsText),
                          ),
                        );
                        await load();
                      })
                    }
                  >
                    <Play size={15} />
                    调用已发布能力
                  </button>
                )}
              </div>
              <p className="muted">
                沙箱不会创建动作或写回业务系统。调用已发布的排产模板会创建待审批草案。
              </p>
            </div>
            {report && (
              <div className="test-report">
                {report.checks.map((c: J) => (
                  <div key={c.name}>
                    <span className={c.passed ? "test-pass" : "test-fail"}>
                      {c.passed ? "PASS" : "FAIL"}
                    </span>
                    <code>{c.name}</code>
                    <small>{c.status || ""}</small>
                  </div>
                ))}
              </div>
            )}
          </section>
          <section className="panel">
            <div className="panel-heading">
              <h2>结果与血缘</h2>
            </div>
            {result ? (
              <pre className="contract-preview large">
                {JSON.stringify(result, null, 2)}
              </pre>
            ) : (
              <div className="empty-state">
                运行预览或已发布能力，查看实际返回。
              </div>
            )}
          </section>
        </div>
      )}
      {current && (edit || approve) && tab === "versions" && (
        <>
          <div className="studio-toolbar">
            <p className="muted">
              批准人必须独立于创建者。发布前会再次核对当前快照与沙箱测试证据。
            </p>
            <div className="studio-actions">
              {approve && generated && (
                <button
                  className="primary"
                  disabled={busy}
                  onClick={() =>
                    action(async () => {
                      await api(`/v1/capabilities/${selected}/publish`, {});
                      await load();
                      setNotice("REST 与 MCP 已共同启用此版本。");
                    })
                  }
                >
                  <ShieldCheck size={16} />
                  审核并发布 v{generated.version}
                </button>
              )}
              <button
                disabled={busy}
                onClick={() =>
                  action(async () => {
                    setDetail(await api("/v1/generated/openapi.json"));
                    setModal("contract");
                  })
                }
              >
                查看动态 OpenAPI
              </button>
            </div>
          </div>
          <div className="panel">
            <table>
              <thead>
                <tr>
                  <th>版本</th>
                  <th>状态</th>
                  <th>本体 / 测试</th>
                  <th>发布人</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {versions.map((v) => (
                  <tr key={v.id}>
                    <td>
                      <strong>v{v.version}</strong>
                      {current.active_version === v.version && (
                        <span className="badge">当前</span>
                      )}
                    </td>
                    <td>{v.status === "published" ? "已发布" : "已生成"}</td>
                    <td>
                      本体 v{v.spec.ontology_version}
                      <small className="block">
                        {v.test_id ? "已有测试记录" : "待测试"}
                      </small>
                    </td>
                    <td>
                      {v.published_by || "—"}
                      <small className="block">
                        {stamp(v.published_at || v.generated_at)}
                      </small>
                    </td>
                    <td>
                      <button
                        onClick={() => {
                          setDetail(v);
                          setModal("contract");
                        }}
                      >
                        查看定义
                      </button>
                      {approve &&
                        v.status === "published" &&
                        v.version !== current.active_version && (
                          <button
                            onClick={() => {
                              setDetail(v);
                              setReason("");
                              setModal("rollback");
                            }}
                          >
                            <History size={14} />
                            回滚
                          </button>
                        )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {active && (
            <section className="panel">
              <div className="panel-heading">
                <h2>Agent 接入</h2>
              </div>
              <p>
                REST：<code>/api/v1/invoke/{selected}</code>
              </p>
              <p>
                MCP：独立网关的 <code>/mcp</code> 地址，通过平台 Bearer
                令牌访问。工具目录会按调用角色和已发布版本动态返回。
              </p>
              <p className="muted">
                部署地址由管理员配置；同一能力在两个协议中共用执行逻辑与审计。
              </p>
            </section>
          )}
        </>
      )}
      {(edit || approve) && tab === "operations" && operations && (
        <>
          <div className="factory-metrics">
            <div>
              <Activity />
              <strong>{operations.count}</strong>
              <span>最近调用</span>
            </div>
            <div>
              <strong>{operations.errors}</strong>
              <span>失败调用</span>
            </div>
            <div>
              <strong>{operations.average_ms} ms</strong>
              <span>平均耗时</span>
            </div>
          </div>
          <div className="panel">
            <table>
              <thead>
                <tr>
                  <th>时间 / 调用人</th>
                  <th>能力版本</th>
                  <th>协议</th>
                  <th>耗时</th>
                  <th>结果</th>
                </tr>
              </thead>
              <tbody>
                {operations.calls.map((c: J) => (
                  <tr key={c.id}>
                    <td>
                      {stamp(c.at)}
                      <small className="block">{c.actor}</small>
                    </td>
                    <td>
                      <code>{c.version}</code>
                    </td>
                    <td>{c.transport.toUpperCase()}</td>
                    <td>{c.duration_ms} ms</td>
                    <td>
                      {c.status === "succeeded"
                        ? "成功"
                        : `${c.error_status} · ${c.error}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {(edit || approve) && tab === "actions" && (
        <div className="panel">
          <div className="panel-heading">
            <h2>排产调整草案</h2>
            <span>不向 ERP / MES 写回</span>
          </div>
          {actions.map((a) => (
            <div className="identity-review" key={a.id}>
              <div>
                <strong>
                  {a.result.proposal.order_id} · 增加工时{" "}
                  {a.result.proposal.extra_hours} h
                </strong>
                <p>{a.result.proposal.note}</p>
                <small>
                  {a.created_by} · {stamp(a.created_at)}
                </small>
              </div>
              <span>{a.result.status}</span>
              <button
                onClick={() => {
                  setDetail(a);
                  setModal("contract");
                }}
              >
                查看依据
              </button>
              {approve && a.result.status === "pending_approval" && (
                <button
                  onClick={() => {
                    setDetail(a);
                    setReason("");
                    setModal("decision");
                  }}
                >
                  审批草案
                </button>
              )}
            </div>
          ))}
          {!actions.length && <div className="empty-state">暂无排产草案。</div>}
        </div>
      )}
      {modal === "create" && form && (
        <StudioModal title="配置业务能力" close={() => setModal("")}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              action(async () => {
                const value = await api("/v1/capabilities", form);
                await load(value.name);
                setTab("design");
                setModal("");
                setNotice("能力草案已保存，下一步生成契约并运行测试。");
              });
            }}
          >
            <div className="form-grid">
              <label>
                工具名称
                <input
                  required
                  pattern="[A-Za-z][A-Za-z0-9_]*"
                  maxLength={64}
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </label>
              <label>
                业务名称
                <input
                  required
                  value={form.title}
                  onChange={(e) => setForm({ ...form, title: e.target.value })}
                />
              </label>
              <label>
                已发布本体
                <select
                  required
                  value={form.ontology_id}
                  onChange={(e) =>
                    action(async () => {
                      const vs = await api(
                        `/v1/ontologies/${e.target.value}/versions`,
                      );
                      setPublished(vs);
                      setForm({
                        ...form,
                        ontology_id: e.target.value,
                        ontology_version: vs.at(-1)?.version,
                      });
                    })
                  }
                >
                  {ontologies
                    .filter((o) => o.active_version)
                    .map((o) => (
                      <option key={o.id} value={o.id}>
                        {o.spec.name}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                固定本体版本
                <select
                  value={form.ontology_version}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      ontology_version: Number(e.target.value),
                    })
                  }
                >
                  {published.map((v) => (
                    <option key={v.id} value={v.version}>
                      v{v.version} · {stamp(v.published_at)}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                受控业务模板
                <select
                  value={form.template}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      template: e.target.value,
                      roles:
                        e.target.value === "schedule_draft"
                          ? form.roles.filter((r: string) => r !== "viewer")
                          : form.roles,
                    })
                  }
                >
                  {templates.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.description}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                根对象
                <select
                  value={form.root_object}
                  disabled={form.template !== "entity_lookup"}
                  onChange={(e) =>
                    setForm({ ...form, root_object: e.target.value })
                  }
                >
                  {published
                    .find((v) => v.version === form.ontology_version)
                    ?.spec.objects.map((o: J) => (
                      <option key={o.name}>{o.name}</option>
                    ))}
                </select>
              </label>
              <label>
                最大结果数
                <input
                  type="number"
                  min={1}
                  max={1000}
                  required
                  value={form.max_results}
                  onChange={(e) =>
                    setForm({ ...form, max_results: Number(e.target.value) })
                  }
                />
              </label>
              <label>
                时间预算（秒）
                <input
                  type="number"
                  min={1}
                  max={30}
                  required
                  value={form.timeout_seconds}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      timeout_seconds: Number(e.target.value),
                    })
                  }
                />
              </label>
            </div>
            <label>
              能力含义
              <textarea
                required
                minLength={5}
                value={form.description}
                onChange={(e) =>
                  setForm({ ...form, description: e.target.value })
                }
              />
            </label>
            <fieldset>
              <legend>允许调用的角色</legend>
              <div className="studio-actions">
                {["viewer", "operator", "approver", "admin"].map((role) => (
                  <label key={role} className="check-label">
                    <input
                      type="checkbox"
                      checked={form.roles.includes(role)}
                      disabled={
                        role === "viewer" && form.template === "schedule_draft"
                      }
                      onChange={(e) =>
                        setForm({
                          ...form,
                          roles: e.target.checked
                            ? [...form.roles, role]
                            : form.roles.filter((r: string) => r !== role),
                        })
                      }
                    />
                    {role}
                  </label>
                ))}
              </div>
            </fieldset>
            <div className="modal-actions">
              <button className="primary" disabled={busy || !form.roles.length}>
                保存能力草案
              </button>
            </div>
          </form>
        </StudioModal>
      )}
      {modal === "contract" && (
        <StudioModal title="契约与执行证据" close={() => setModal("")}>
          <pre className="contract-preview large">
            {JSON.stringify(detail, null, 2)}
          </pre>
        </StudioModal>
      )}
      {modal === "rollback" && (
        <StudioModal
          title={`回滚能力到 v${detail.version}`}
          close={() => setModal("")}
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              action(async () => {
                await api(`/v1/capabilities/${selected}/rollback`, {
                  version: detail.version,
                  reason,
                });
                await load();
                setModal("");
              });
            }}
          >
            <label>
              回滚原因
              <textarea
                required
                minLength={3}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
            </label>
            <div className="modal-actions">
              <button className="primary" disabled={busy}>
                确认回滚
              </button>
            </div>
          </form>
        </StudioModal>
      )}
      {modal === "decision" && (
        <StudioModal title="审批排产草案" close={() => setModal("")}>
          <p>批准仅表示草案获认可；不会提交生产系统，也不会报告执行成功。</p>
          <label>
            审批意见
            <textarea
              minLength={3}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          <div className="modal-actions">
            {["reject", "approve"].map((decision) => (
              <button
                key={decision}
                className={decision === "approve" ? "primary" : ""}
                disabled={busy || reason.length < 3}
                onClick={() =>
                  action(async () => {
                    await api(`/v1/actions/${detail.id}/decision`, {
                      decision,
                      reason,
                    });
                    await load();
                    setModal("");
                  })
                }
              >
                {decision === "approve" ? "批准草案" : "驳回"}
              </button>
            ))}
          </div>
        </StudioModal>
      )}
    </div>
  );
}
