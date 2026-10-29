import { useEffect, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
  type Connection,
  useNodesState,
  type Node,
} from "@xyflow/react";
import {
  Plus,
  Save,
  ShieldCheck,
  RefreshCw,
  GitBranch,
  CheckCircle2,
  AlertTriangle,
  History,
  Search,
  ArrowRight,
  Trash2,
  Code2,
} from "lucide-react";
import StudioModal from "./StudioModal";
type J = any;
type API = (path: string, body?: J) => Promise<J>;
const stamp = (s?: string) => (s ? new Date(s).toLocaleString("zh-CN") : "—");
const freshAttribute = () => ({
  name: "",
  label: "",
  type: "string",
  required: true,
  enum: [],
  minimum: null,
  maximum: null,
  unit: null,
});
function EntityNode({ data }: { data: J }) {
  return (
    <div className="ontology-entity">
      <Handle type="target" position={Position.Left} />
      <span className="eyebrow">BUSINESS OBJECT</span>
      <strong>{data.object.label}</strong>
      <code>{data.object.name}</code>
      <div>
        {data.object.attributes.slice(0, 5).map((a: J) => (
          <p key={a.name}>
            <span>
              {a.name === data.object.identity ? "◆ " : ""}
              {a.name}
            </span>
            <small>
              {a.type}
              {a.unit ? ` · ${a.unit}` : ""}
            </small>
          </p>
        ))}
        {data.object.attributes.length > 5 && (
          <small>+ {data.object.attributes.length - 5} 个属性</small>
        )}
      </div>
      <footer>{data.mappings} 个映射 · 点击编辑</footer>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
const nodeTypes = { entity: EntityNode };
export default function OntologyStudio({
  api,
  principal,
}: {
  api: API;
  principal: J;
}) {
  const edit = ["operator", "admin"].includes(principal.role),
    approve = ["approver", "admin"].includes(principal.role);
  const [ontologies, setOntologies] = useState<J[]>([]),
    [mappings, setMappings] = useState<J[]>([]),
    [pipelines, setPipelines] = useState<J[]>([]),
    [reviews, setReviews] = useState<J[]>([]);
  const [selected, setSelected] = useState(""),
    [draft, setDraft] = useState<J>(null),
    [dirty, setDirty] = useState(false),
    [tab, setTab] = useState("canvas"),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [quality, setQuality] = useState<J>(null),
    [versions, setVersions] = useState<J[]>([]),
    [snapshot, setSnapshot] = useState<J>(null),
    [modal, setModal] = useState("");
  const [object, setObject] = useState<J>(null),
    [objectIndex, setObjectIndex] = useState(-1),
    [relation, setRelation] = useState<J>(null),
    [relationIndex, setRelationIndex] = useState(-1),
    [suggestion, setSuggestion] = useState<J>({ object: "", pipeline_id: "" }),
    [mapping, setMapping] = useState<J>(null),
    [columns, setColumns] = useState<string[]>([]),
    [jsonText, setJsonText] = useState(""),
    [identity, setIdentity] = useState<J>({
      mapping_id: "",
      source_key: "",
      target_mapping_id: "",
      target_source_key: "",
      reason: "",
    }),
    [filter, setFilter] = useState("");
  const current = ontologies.find((o) => o.id === selected),
    ownMappings = mappings.filter((m) => m.ontology_id === selected);
  async function load(id = selected) {
    if (!edit && !approve) return;
    const [os, ms, rs] = await Promise.all([
      api("/v1/ontologies"),
      api("/v1/mappings"),
      api("/v1/identities/reviews"),
    ]);
    setOntologies(os);
    setMappings(ms);
    setReviews(rs);
    if (edit) setPipelines(await api("/v1/pipelines"));
    const key = id || os[0]?.id || "";
    setSelected(key);
    if (key) setVersions(await api(`/v1/ontologies/${key}/versions`));
  }
  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, [principal.user]);
  useEffect(() => {
    setDraft(current ? structuredClone(current.spec) : null);
    setDirty(false);
    setQuality(null);
    setSnapshot(null);
  }, [selected, current?.revision]);
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
  function change(spec: J) {
    setDraft(spec);
    setDirty(true);
    setQuality(null);
  }
  async function save(spec = draft) {
    const result = await api(`/v1/ontologies/${selected}/draft`, {
      revision: current.revision,
      spec,
    });
    setOntologies((old) => old.map((o) => (o.id === result.id ? result : o)));
    setDraft(result.spec);
    setDirty(false);
    return result;
  }
  async function make() {
    const template = await api("/v1/ontology-template");
    const value = await api("/v1/ontologies", template);
    await load(value.id);
    setNotice("已创建制造业模板。请关联已同步的数据资产并逐项确认映射。");
  }
  async function addMapping(body: J) {
    const result = await api("/v1/mappings", body);
    const next = {
      ...draft,
      mapping_ids: [
        ...draft.mapping_ids.filter((id: string) => {
          const m = ownMappings.find((x) => x.id === id);
          return (
            !m ||
            m.pipeline_id !== result.pipeline_id ||
            m.object !== result.object
          );
        }),
        result.id,
      ],
    };
    await save(next);
    await load();
    setModal("");
    setNotice("映射草案已关联，等待独立审批人确认。");
  }
  async function editMapping(value: J) {
    setMapping(structuredClone(value));
    if (edit) {
      const p = pipelines.find((p) => p.id === value.pipeline_id);
      if (p) {
        const tables = await api(`/v1/sources/${p.source_id}/discover`, {});
        setColumns(
          tables
            .find((t: J) => t.name === p.table)
            ?.columns.map((c: J) => c.name) || [],
        );
      }
    }
    setModal("mapping");
  }
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  useEffect(() => {
    setNodes(
      (previous) =>
        draft?.objects.map((o: J) => ({
          ...previous.find((n) => n.id === o.name),
          id: o.name,
          type: "entity",
          position: o.position,
          data: {
            object: o,
            mappings: mappings.filter(
              (m) =>
                m.ontology_id === selected &&
                m.object === o.name &&
                draft.mapping_ids.includes(m.id),
            ).length,
          },
        })) || [],
    );
  }, [draft, mappings, selected, setNodes]);
  const edges =
    draft?.relations.map((r: J) => ({
      id: r.name,
      source: r.from_object,
      target: r.to_object,
      label: r.label,
      style: { stroke: "#53967b", strokeWidth: 2 },
    })) || [];
  function newRelation(connection?: Connection) {
    setRelationIndex(-1);
    setRelation({
      name: "",
      label: "",
      from_object: connection?.source || draft.objects[0]?.name,
      to_object:
        connection?.target || draft.objects[1]?.name || draft.objects[0]?.name,
      from_field: "",
      to_field: "id",
      cardinality: "many_to_one",
      required: true,
      evidence: "",
      via_object: null,
      via_from_field: null,
      via_to_field: null,
    });
    setModal("relation");
  }
  if (!edit && !approve)
    return (
      <div className="empty-state">
        <ShieldCheck />
        <h3>本体设计需要工程师或审批角色</h3>
        <p>已发布业务能力通过能力网关提供。</p>
      </div>
    );
  return (
    <div className="v1-studio ontology-studio">
      <div className="studio-toolbar">
        <div className="studio-actions">
          <select
            aria-label="选择本体"
            value={selected}
            onChange={(e) =>
              action(async () => {
                if (dirty) {
                  setError("请先保存草案后切换本体。");
                  return;
                }
                await load(e.target.value);
              })
            }
          >
            <option value="">选择本体</option>
            {ontologies.map((o) => (
              <option key={o.id} value={o.id}>
                {o.spec.name}
              </option>
            ))}
          </select>
          {current && (
            <span className="badge">
              草案 r{current.revision} · 发布 v{current.active_version || "—"}
            </span>
          )}
        </div>
        <div className="studio-actions">
          <button
            onClick={() => action(() => load())}
            disabled={busy || dirty}
            aria-label="刷新本体列表"
          >
            <RefreshCw size={16} />
          </button>
          {edit && (
            <button onClick={() => action(make)} disabled={busy || dirty}>
              <Plus size={16} />
              制造业模板
            </button>
          )}
          {edit && draft && (
            <button
              className="primary"
              disabled={!dirty || busy}
              onClick={() =>
                action(async () => {
                  await save();
                  setNotice("草案已保存，已发布版本保持可用。");
                })
              }
            >
              <Save size={16} />
              保存草案{dirty ? " *" : ""}
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
      {!draft ? (
        <div className="empty-state">
          <GitBranch size={36} />
          <h3>把数据资产变成统一业务对象</h3>
          <p>
            从制造业模板开始，连接订单、工单与库存，审阅身份和关系证据后发布。
          </p>
          {edit && (
            <button
              className="primary"
              disabled={busy}
              onClick={() => action(make)}
            >
              创建制造业本体
            </button>
          )}
        </div>
      ) : (
        <>
          <div className="tabs">
            {[
              ["canvas", "对象与关系"],
              ["mappings", "AI 映射"],
              ["quality", "质量与身份"],
              ["versions", "发布版本"],
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
          {tab === "canvas" && (
            <>
              <div className="studio-toolbar">
                <p className="muted">
                  拖拽对象调整布局；从节点连线建立关系。更改需保存并重新审批发布。
                </p>
                {edit && (
                  <div className="studio-actions">
                    <button
                      onClick={() => {
                        setObjectIndex(-1);
                        setObject({
                          name: "",
                          label: "",
                          identity: "id",
                          attributes: [{ ...freshAttribute(), name: "id" }],
                          position: { x: 0, y: 0 },
                        });
                        setModal("object");
                      }}
                    >
                      <Plus size={15} />
                      业务对象
                    </button>
                    <button onClick={() => newRelation()}>
                      <GitBranch size={15} />
                      关系
                    </button>
                    <button
                      onClick={() => {
                        setJsonText(JSON.stringify(draft, null, 2));
                        setModal("dsl");
                      }}
                    >
                      <Code2 size={15} />
                      DSL / 指标
                    </button>
                  </div>
                )}
              </div>
              <div className="ontology-canvas">
                <ReactFlow
                  nodes={nodes}
                  edges={edges}
                  nodeTypes={nodeTypes}
                  fitView
                  nodesDraggable={edit}
                  nodesConnectable={edit}
                  onNodesChange={onNodesChange}
                  onNodeDragStop={(_, node) =>
                    change({
                      ...draft,
                      objects: draft.objects.map((o: J) =>
                        o.name === node.id
                          ? { ...o, position: node.position }
                          : o,
                      ),
                    })
                  }
                  onNodeClick={(_, node) => {
                    const index = draft.objects.findIndex(
                      (o: J) => o.name === node.id,
                    );
                    setObjectIndex(index);
                    setObject(structuredClone(draft.objects[index]));
                    setModal("object");
                  }}
                  onEdgeClick={(_, edge) => {
                    const index = draft.relations.findIndex(
                      (r: J) => r.name === edge.id,
                    );
                    setRelationIndex(index);
                    setRelation(structuredClone(draft.relations[index]));
                    setModal("relation");
                  }}
                  onConnect={newRelation}
                >
                  <Background color="#cfded2" gap={24} />
                  <Controls />
                </ReactFlow>
              </div>
              <div className="panel">
                <div className="panel-heading">
                  <h2>关系证据</h2>
                  <span>{draft.relations.length} 条定义</span>
                </div>
                <table>
                  <thead>
                    <tr>
                      <th>业务关系</th>
                      <th>引用条件</th>
                      <th>基数</th>
                      <th>确认依据</th>
                    </tr>
                  </thead>
                  <tbody>
                    {draft.relations.map((r: J, i: number) => (
                      <tr
                        key={r.name}
                        onClick={() => {
                          setRelationIndex(i);
                          setRelation(structuredClone(r));
                          setModal("relation");
                        }}
                      >
                        <td>{r.label}</td>
                        <td>
                          <code>
                            {r.from_object}.{r.from_field} → {r.to_object}.
                            {r.to_field}
                          </code>
                        </td>
                        <td>{r.cardinality}</td>
                        <td>{r.evidence}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          {tab === "mappings" && (
            <>
              <div className="studio-toolbar">
                <p className="muted">
                  候选不自动生效。批准后仍需发布本体版本。
                </p>
                {edit && (
                  <button
                    className="primary"
                    disabled={busy || dirty}
                    onClick={() => {
                      setSuggestion({
                        object: draft.objects[0]?.name,
                        pipeline_id: pipelines[0]?.id || "",
                      });
                      setModal("suggestion");
                    }}
                  >
                    <Plus size={16} />
                    生成映射候选
                  </button>
                )}
              </div>
              <div className="panel">
                <table>
                  <thead>
                    <tr>
                      <th>对象 / 来源流水线</th>
                      <th>方式与解释</th>
                      <th>状态</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ownMappings.map((m) => (
                      <tr key={m.id}>
                        <td>
                          <strong>{m.object}</strong>
                          <small className="block">
                            {pipelines.find((p) => p.id === m.pipeline_id)
                              ?.name || m.pipeline_id}
                          </small>
                          <small className="block">
                            {draft.mapping_ids.includes(m.id)
                              ? "已关联当前草案"
                              : "尚未关联"}
                          </small>
                        </td>
                        <td>
                          <span className="badge">
                            {m.provider === "mock"
                              ? "MOCK · 词典候选"
                              : m.provider === "manual"
                                ? "人工修订"
                                : "配置的 LLM"}
                          </span>
                          <p>{m.explanation}</p>
                        </td>
                        <td>
                          {m.status === "approved" ? "已批准" : "待审核"}
                          <small className="block">
                            {m.approved_by || m.created_by}
                          </small>
                        </td>
                        <td>
                          <div className="studio-actions">
                            <button
                              onClick={() => action(() => editMapping(m))}
                            >
                              审阅字段
                            </button>
                            {approve && m.status !== "approved" && (
                              <button
                                disabled={busy}
                                onClick={() =>
                                  action(async () => {
                                    await api(
                                      `/v1/mappings/${m.id}/approve`,
                                      {},
                                    );
                                    await load();
                                  })
                                }
                              >
                                批准
                              </button>
                            )}
                            {edit && !draft.mapping_ids.includes(m.id) && (
                              <button
                                disabled={busy || dirty}
                                onClick={() =>
                                  action(async () => {
                                    await save({
                                      ...draft,
                                      mapping_ids: [...draft.mapping_ids, m.id],
                                    });
                                    await load();
                                  })
                                }
                              >
                                关联草案
                              </button>
                            )}
                            {edit && draft.mapping_ids.includes(m.id) && (
                              <button
                                onClick={() =>
                                  change({
                                    ...draft,
                                    mapping_ids: draft.mapping_ids.filter(
                                      (id: string) => id !== m.id,
                                    ),
                                  })
                                }
                              >
                                移出草案
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!ownMappings.length && (
                  <div className="empty-state">
                    先同步来源数据，再生成候选映射。
                  </div>
                )}
              </div>
            </>
          )}
          {tab === "quality" && (
            <>
              <div className="studio-toolbar">
                <p className="muted">
                  检查字段约束、重复身份、孤儿记录与关系歧义。
                </p>
                <div className="studio-actions">
                  <button
                    className="primary"
                    disabled={dirty || busy}
                    onClick={() =>
                      action(async () =>
                        setQuality(
                          await api(`/v1/ontologies/${selected}/validate`, {}),
                        ),
                      )
                    }
                  >
                    <CheckCircle2 size={16} />
                    运行质量检查
                  </button>
                  {edit && (
                    <button
                      disabled={busy || dirty}
                      onClick={() => {
                        setIdentity({
                          mapping_id: "",
                          source_key: "",
                          target_mapping_id: "",
                          target_source_key: "",
                          reason: "",
                        });
                        setModal("identity");
                      }}
                    >
                      创建身份复核
                    </button>
                  )}
                </div>
              </div>
              {quality && (
                <>
                  <div
                    className={quality.valid ? "studio-notice" : "error-banner"}
                  >
                    {quality.valid
                      ? "检查通过，可由独立审批人发布。"
                      : "发现质量问题，发布已被阻止。"}{" "}
                    {quality.counts.entities} 个实体 · {quality.counts.aliases}{" "}
                    个来源身份 · {quality.counts.relationships} 条有据关系
                  </div>
                  <div className="panel">
                    <table>
                      <thead>
                        <tr>
                          <th>级别 / 代码</th>
                          <th>发现</th>
                          <th>证据引用</th>
                        </tr>
                      </thead>
                      <tbody>
                        {quality.issues.map((i: J, n: number) => (
                          <tr key={n}>
                            <td>
                              <AlertTriangle size={14} /> {i.severity} /{" "}
                              {i.code}
                            </td>
                            <td>{i.message}</td>
                            <td>
                              <code>{i.evidence.join("\n")}</code>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div className="panel">
                    <div className="panel-heading">
                      <h2>来源身份与统一 ID</h2>
                    </div>
                    <table>
                      <thead>
                        <tr>
                          <th>对象</th>
                          <th>来源主键</th>
                          <th>统一身份</th>
                          <th>映射</th>
                        </tr>
                      </thead>
                      <tbody>
                        {quality.aliases
                          .slice(0, 100)
                          .map((a: J, i: number) => (
                            <tr key={i}>
                              <td>{a.entity_type}</td>
                              <td>{a.source_primary_key}</td>
                              <td>
                                <code>{a.canonical_id}</code>
                              </td>
                              <td>
                                <code>{a.mapping_id}</code>
                              </td>
                            </tr>
                          ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
              <div className="panel">
                <div className="panel-heading">
                  <h2>身份复核队列</h2>
                </div>
                {reviews
                  .filter((r) => r.ontology_id === selected)
                  .map((r) => (
                    <div className="identity-review" key={r.id}>
                      <div>
                        <strong>
                          {r.source_key} → {r.target_source_key}
                        </strong>
                        <p>{r.reason}</p>
                        <small>
                          {r.created_by} · {stamp(r.created_at)}
                        </small>
                      </div>
                      <span>
                        {r.status === "approved" ? "已批准" : r.status === "revoked" ? "已撤销" : "待审批"}
                      </span>
                      {approve && r.status === "draft" && (
                        <button
                          disabled={busy}
                          onClick={() =>
                            action(async () => {
                              await api(
                                `/v1/identities/reviews/${r.id}/approve`,
                                {},
                              );
                              await load();
                            })
                          }
                        >
                          批准身份合并
                        </button>
                      )}
                      {approve && r.status === "approved" && (
                        <button disabled={busy} onClick={() => {
                          setIdentity(r); setJsonText(""); setModal("revoke-identity");
                        }}>撤销决议</button>
                      )}
                    </div>
                  ))}
              </div>
            </>
          )}
          {tab === "versions" && (
            <>
              <div className="studio-toolbar">
                <p className="muted">
                  每个发布版本固定
                  DSL、批准映射及身份决议。草案更改不会影响已发布能力。
                </p>
                <div className="studio-actions">
                  {approve && (
                    <button
                      className="primary"
                      disabled={busy || dirty}
                      onClick={() =>
                        action(async () => {
                          await api(`/v1/ontologies/${selected}/publish`, {
                            revision: current.revision,
                          });
                          await load();
                          setNotice("本体发布成功，可以在能力工厂引用此版本。");
                        })
                      }
                    >
                      <ShieldCheck size={16} />
                      审核并发布 r{current.revision}
                    </button>
                  )}
                  {edit && current.active_version && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        action(async () => {
                          const result = await api(
                            `/v1/ontologies/${selected}/refresh`,
                            {},
                          );
                          setQuality(result);
                          setTab("quality");
                          await load();
                        })
                      }
                    >
                      <RefreshCw size={16} />
                      刷新已发布数据
                    </button>
                  )}
                </div>
              </div>
              <div className="panel">
                <table>
                  <thead>
                    <tr>
                      <th>版本</th>
                      <th>发布人 / 时间</th>
                      <th>指纹与刷新状态</th>
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
                          <small className="block">草案 r{v.revision}</small>
                        </td>
                        <td>
                          {v.published_by}
                          <small className="block">
                            {stamp(v.published_at)}
                          </small>
                        </td>
                        <td>
                          <code>{v.digest.slice(0, 16)}</code>
                          <small className="block">
                            {v.refresh_valid === false
                              ? "最近刷新未通过质量检查"
                              : "最近快照可用"}
                          </small>
                        </td>
                        <td>
                          <button
                            disabled={busy}
                            onClick={() =>
                              action(async () =>
                                setSnapshot(
                                  await api(
                                    `/v1/ontologies/${selected}/snapshot?version=${v.version}`,
                                  ),
                                ),
                              )
                            }
                          >
                            <Search size={14} />
                            查看数据
                          </button>
                          {approve && current.active_version !== v.version && (
                            <button
                              disabled={busy}
                              onClick={() => {
                                setJsonText("");
                                setRelation({ version: v.version });
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
              {snapshot && (
                <div className="panel">
                  <div className="panel-heading">
                    <h2>已发布实体快照</h2>
                    <input
                      aria-label="筛选实体"
                      placeholder="按编号、对象或内容筛选"
                      value={filter}
                      onChange={(e) => setFilter(e.target.value)}
                    />
                  </div>
                  <p className="muted">
                    {stamp(snapshot.built_at)} · {snapshot.counts.entities}{" "}
                    个实体（最多展示 100 个）
                  </p>
                  {snapshot.entities
                    .filter((e: J) =>
                      JSON.stringify(e)
                        .toLowerCase()
                        .includes(filter.toLowerCase()),
                    )
                    .slice(0, 100)
                    .map((e: J) => (
                      <details className="entity-record" key={e.id}>
                        <summary>
                          {e.entity_type} · {e.data.id || e.id}
                          <span className="badge">
                            {e.provenance.length} 个来源
                          </span>
                        </summary>
                        <pre>{JSON.stringify(e, null, 2)}</pre>
                      </details>
                    ))}
                </div>
              )}
            </>
          )}
        </>
      )}
      {modal === "object" && object && (
        <StudioModal
          title={objectIndex < 0 ? "添加业务对象" : "编辑业务对象"}
          close={() => setModal("")}
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const next = structuredClone(draft);
              if (objectIndex < 0) next.objects.push(object);
              else next.objects[objectIndex] = object;
              change(next);
              setModal("");
            }}
          >
            <div className="form-grid">
              <label>
                对象名称
                <input
                  required
                  disabled={!edit || objectIndex >= 0}
                  pattern="[A-Za-z][A-Za-z0-9_]*"
                  value={object.name}
                  onChange={(e) =>
                    setObject({ ...object, name: e.target.value })
                  }
                />
              </label>
              <label>
                业务名称
                <input
                  required
                  disabled={!edit}
                  value={object.label}
                  onChange={(e) =>
                    setObject({ ...object, label: e.target.value })
                  }
                />
              </label>
              <label>
                业务身份属性
                <select
                  disabled={!edit}
                  value={object.identity}
                  onChange={(e) =>
                    setObject({ ...object, identity: e.target.value })
                  }
                >
                  {object.attributes.map((a: J, i: number) => (
                    <option key={i} value={a.name}>
                      {a.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <h3>属性定义</h3>
            {object.attributes.map((a: J, i: number) => (
              <div className="attribute-editor" key={i}>
                <input
                  aria-label={`属性 ${i + 1} 名称`}
                  required
                  disabled={!edit}
                  placeholder="属性名"
                  pattern="[A-Za-z][A-Za-z0-9_]*"
                  value={a.name}
                  onChange={(e) =>
                    setObject({
                      ...object,
                      attributes: object.attributes.map((x: J, n: number) =>
                        n === i ? { ...x, name: e.target.value } : x,
                      ),
                    })
                  }
                />
                <select
                  aria-label={`属性 ${i + 1} 类型`}
                  disabled={!edit}
                  value={a.type}
                  onChange={(e) =>
                    setObject({
                      ...object,
                      attributes: object.attributes.map((x: J, n: number) =>
                        n === i ? { ...x, type: e.target.value } : x,
                      ),
                    })
                  }
                >
                  {[
                    "string",
                    "integer",
                    "number",
                    "boolean",
                    "date",
                    "datetime",
                  ].map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
                <input
                  aria-label={`属性 ${i + 1} 单位`}
                  disabled={!edit}
                  placeholder="单位（kg、m…）"
                  value={a.unit || ""}
                  onChange={(e) =>
                    setObject({
                      ...object,
                      attributes: object.attributes.map((x: J, n: number) =>
                        n === i ? { ...x, unit: e.target.value || null } : x,
                      ),
                    })
                  }
                />
                <input
                  aria-label={`属性 ${i + 1} 枚举`}
                  disabled={!edit}
                  placeholder="枚举，逗号分隔"
                  value={a.enum?.join(",") || ""}
                  onChange={(e) =>
                    setObject({
                      ...object,
                      attributes: object.attributes.map((x: J, n: number) =>
                        n === i
                          ? {
                              ...x,
                              enum: e.target.value
                                ? e.target.value.split(",")
                                : [],
                            }
                          : x,
                      ),
                    })
                  }
                />
                <label>
                  <input
                    type="checkbox"
                    disabled={!edit}
                    checked={a.required}
                    onChange={(e) =>
                      setObject({
                        ...object,
                        attributes: object.attributes.map((x: J, n: number) =>
                          n === i ? { ...x, required: e.target.checked } : x,
                        ),
                      })
                    }
                  />
                  必填
                </label>
                {edit && (
                  <button
                    type="button"
                    aria-label={`删除属性 ${a.name}`}
                    onClick={() =>
                      setObject({
                        ...object,
                        attributes: object.attributes.filter(
                          (_: J, n: number) => n !== i,
                        ),
                      })
                    }
                  >
                    <Trash2 size={14} />
                  </button>
                )}
              </div>
            ))}
            {edit && (
              <>
                <button
                  type="button"
                  onClick={() =>
                    setObject({
                      ...object,
                      attributes: [...object.attributes, freshAttribute()],
                    })
                  }
                >
                  <Plus size={14} />
                  增加属性
                </button>
                <div className="modal-actions">
                  <button className="primary">应用到草案</button>
                  {objectIndex >= 0 && (
                    <button
                      type="button"
                      onClick={() => {
                        change({
                          ...draft,
                          objects: draft.objects.filter(
                            (_: J, i: number) => i !== objectIndex,
                          ),
                          relations: draft.relations.filter(
                            (r: J) =>
                              r.from_object !== object.name &&
                              r.to_object !== object.name &&
                              r.via_object !== object.name,
                          ),
                          mapping_ids: draft.mapping_ids.filter(
                            (id: string) =>
                              ownMappings.find((m) => m.id === id)?.object !==
                              object.name,
                          ),
                          metrics: draft.metrics.filter(
                            (m: J) => m.object !== object.name,
                          ),
                        });
                        setModal("");
                      }}
                    >
                      删除对象及其依赖
                    </button>
                  )}
                </div>
              </>
            )}
          </form>
        </StudioModal>
      )}
      {modal === "relation" && relation && (
        <StudioModal title="编辑关系与证据" close={() => setModal("")}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const rs = [...draft.relations];
              if (relationIndex < 0) rs.push(relation);
              else rs[relationIndex] = relation;
              change({ ...draft, relations: rs });
              setModal("");
            }}
          >
            <div className="form-grid">
              {[
                ["name", "关系名称"],
                ["label", "业务名称"],
              ].map(([key, label]) => (
                <label key={key}>
                  {label}
                  <input
                    required
                    disabled={!edit}
                    value={relation[key]}
                    onChange={(e) =>
                      setRelation({ ...relation, [key]: e.target.value })
                    }
                  />
                </label>
              ))}
              {[
                ["from_object", "from_field", "来源对象"],
                ["to_object", "to_field", "目标对象"],
              ].map(([key, field, label]) => (
                <label key={key}>
                  {label}
                  <select
                    disabled={!edit}
                    value={relation[key]}
                    onChange={(e) =>
                      setRelation({
                        ...relation,
                        [key]: e.target.value,
                        [field]: "",
                      })
                    }
                  >
                    {draft.objects.map((o: J) => (
                      <option key={o.name}>{o.name}</option>
                    ))}
                  </select>
                  <select
                    required
                    disabled={!edit}
                    aria-label={label + "关联字段"}
                    value={relation[field]}
                    onChange={(e) =>
                      setRelation({ ...relation, [field]: e.target.value })
                    }
                  >
                    <option value="">选择引用属性</option>
                    {draft.objects
                      .find((o: J) => o.name === relation[key])
                      ?.attributes.map((a: J) => (
                        <option key={a.name}>{a.name}</option>
                      ))}
                  </select>
                </label>
              ))}
              <label>
                关系基数
                <select
                  disabled={!edit}
                  value={relation.cardinality}
                  onChange={(e) =>
                    setRelation({
                      ...relation,
                      cardinality: e.target.value,
                      via_object: null,
                      via_from_field: null,
                      via_to_field: null,
                    })
                  }
                >
                  <option value="many_to_one">
                    多对一（订单可对应多个工单）
                  </option>
                  <option value="one_to_one">一对一</option>
                  <option value="many_to_many">
                    多对多（通过真实关联单据）
                  </option>
                </select>
              </label>
              <label>
                强制完整引用
                <input
                  type="checkbox"
                  disabled={!edit}
                  checked={relation.required}
                  onChange={(e) =>
                    setRelation({ ...relation, required: e.target.checked })
                  }
                />
              </label>
            </div>
            {relation.cardinality === "many_to_many" && (
              <div className="form-grid">
                <label>
                  关联单据对象
                  <select
                    required
                    value={relation.via_object || ""}
                    disabled={!edit}
                    onChange={(e) =>
                      setRelation({ ...relation, via_object: e.target.value })
                    }
                  >
                    <option value="">选择对象</option>
                    {draft.objects.map((o: J) => (
                      <option key={o.name}>{o.name}</option>
                    ))}
                  </select>
                </label>
                {["via_from_field", "via_to_field"].map((key) => (
                  <label key={key}>
                    {key === "via_from_field"
                      ? "单据上的来源引用"
                      : "单据上的目标引用"}
                    <select
                      required
                      disabled={!edit}
                      value={relation[key] || ""}
                      onChange={(e) =>
                        setRelation({ ...relation, [key]: e.target.value })
                      }
                    >
                      <option value="">选择属性</option>
                      {draft.objects
                        .find((o: J) => o.name === relation.via_object)
                        ?.attributes.map((a: J) => (
                          <option key={a.name}>{a.name}</option>
                        ))}
                    </select>
                  </label>
                ))}
              </div>
            )}
            <label>
              关系确认依据
              <textarea
                required
                minLength={5}
                disabled={!edit}
                value={relation.evidence}
                onChange={(e) =>
                  setRelation({ ...relation, evidence: e.target.value })
                }
              />
            </label>
            {edit && (
              <div className="modal-actions">
                <button className="primary">应用到草案</button>
                {relationIndex >= 0 && (
                  <button
                    type="button"
                    onClick={() => {
                      change({
                        ...draft,
                        relations: draft.relations.filter(
                          (_: J, i: number) => i !== relationIndex,
                        ),
                      });
                      setModal("");
                    }}
                  >
                    删除关系
                  </button>
                )}
              </div>
            )}
          </form>
        </StudioModal>
      )}
      {modal === "suggestion" && (
        <StudioModal title="生成字段映射候选" close={() => setModal("")}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              action(async () => {
                const result = await api("/v1/mappings/suggestions", {
                  ontology_id: selected,
                  ...suggestion,
                });
                await load();
                await editMapping(result);
              });
            }}
          >
            <p>
              默认使用明确标注的 mock
              词典候选；管理员可在服务器配置兼容模型接口。候选始终需要人工审核。
            </p>
            <div className="form-grid">
              <label>
                目标对象
                <select
                  value={suggestion.object}
                  onChange={(e) =>
                    setSuggestion({ ...suggestion, object: e.target.value })
                  }
                >
                  {draft.objects.map((o: J) => (
                    <option key={o.name}>{o.name}</option>
                  ))}
                </select>
              </label>
              <label>
                已同步流水线
                <select
                  required
                  value={suggestion.pipeline_id}
                  onChange={(e) =>
                    setSuggestion({
                      ...suggestion,
                      pipeline_id: e.target.value,
                    })
                  }
                >
                  <option value="">选择流水线</option>
                  {pipelines.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                      {p.last_success_at ? " · 已同步" : " · 未同步"}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="modal-actions">
              <button className="primary" disabled={busy}>
                生成并审阅
              </button>
            </div>
          </form>
        </StudioModal>
      )}
      {modal === "mapping" && mapping && (
        <StudioModal
          title={`字段映射 · ${mapping.object}`}
          close={() => setModal("")}
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              action(() =>
                addMapping({
                  ontology_id: selected,
                  object: mapping.object,
                  pipeline_id: mapping.pipeline_id,
                  fields: mapping.fields,
                  explanation: mapping.explanation,
                }),
              );
            }}
          >
            <p>
              {mapping.provider === "mock" ? "MOCK 候选。" : ""}
              保存修订将创建新的待审批映射。
            </p>
            {draft.objects
              .find((o: J) => o.name === mapping.object)
              ?.attributes.map((attr: J) => {
                const rule = mapping.fields[attr.name] || {
                  source: "",
                  transform: attr.type,
                };
                const setRule = (delta: J) =>
                  setMapping({
                    ...mapping,
                    fields: {
                      ...mapping.fields,
                      [attr.name]: { ...rule, ...delta },
                    },
                  });
                return (
                  <div className="mapping-field-editor" key={attr.name}>
                    <label>
                      <strong>
                        {attr.name}
                        {attr.required ? " *" : ""}
                      </strong>
                      <small>
                        {attr.type}
                        {attr.unit ? ` · ${attr.unit}` : ""}
                      </small>
                    </label>
                    <ArrowRight size={14} />
                    {edit ? (
                      <select
                        required={attr.required}
                        value={rule.source}
                        onChange={(e) => {
                          if (!e.target.value) {
                            const fields = { ...mapping.fields };
                            delete fields[attr.name];
                            setMapping({ ...mapping, fields });
                          } else setRule({ source: e.target.value });
                        }}
                      >
                        <option value="">不映射</option>
                        {columns.map((c) => (
                          <option key={c}>{c}</option>
                        ))}
                      </select>
                    ) : (
                      <code>{rule.source}</code>
                    )}
                    <select
                      disabled={!edit}
                      value={rule.transform}
                      onChange={(e) => setRule({ transform: e.target.value })}
                    >
                      {[
                        "identity",
                        "string",
                        "integer",
                        "number",
                        "boolean",
                        "date",
                        "datetime",
                        "enum",
                        "unit",
                      ].map((t) => (
                        <option key={t}>{t}</option>
                      ))}
                    </select>
                    {rule.transform === "enum" && (
                      <label className="full-width">
                        状态对应（每行 来源=标准值）
                        <textarea
                          disabled={!edit}
                          value={Object.entries(rule.values || {})
                            .map(([k, v]) => `${k}=${v}`)
                            .join("\n")}
                          onChange={(e) =>
                            setRule({
                              values: Object.fromEntries(
                                e.target.value
                                  .split("\n")
                                  .filter(Boolean)
                                  .map((s) => {
                                    const i = s.indexOf("=");
                                    return i < 0
                                      ? [s, ""]
                                      : [s.slice(0, i), s.slice(i + 1)];
                                  }),
                              ),
                            })
                          }
                        />
                      </label>
                    )}
                    {rule.transform === "unit" && (
                      <div className="form-grid full-width">
                        <label>
                          来源单位
                          <input
                            required
                            disabled={!edit}
                            value={rule.source_unit || ""}
                            onChange={(e) =>
                              setRule({ source_unit: e.target.value })
                            }
                          />
                        </label>
                        <label>
                          标准单位
                          <input
                            required
                            disabled={!edit}
                            value={rule.target_unit || ""}
                            onChange={(e) =>
                              setRule({ target_unit: e.target.value })
                            }
                          />
                        </label>
                      </div>
                    )}
                  </div>
                );
              })}
            <label>
              映射解释
              <textarea
                required
                minLength={3}
                disabled={!edit}
                value={mapping.explanation}
                onChange={(e) =>
                  setMapping({ ...mapping, explanation: e.target.value })
                }
              />
            </label>
            {edit && (
              <div className="modal-actions">
                <button className="primary" disabled={busy}>
                  保存修订并关联草案
                </button>
              </div>
            )}
          </form>
        </StudioModal>
      )}
      {modal === "dsl" && (
        <StudioModal title="高级 DSL 与指标规则" close={() => setModal("")}>
          <p>
            仅支持声明式对象、关系和 count / sum / ratio
            指标，服务端会拒绝未知或可执行内容。
          </p>
          <textarea
            className="dsl-editor"
            aria-label="本体 DSL"
            value={jsonText}
            onChange={(e) => setJsonText(e.target.value)}
          />
          <div className="modal-actions">
            <button
              className="primary"
              onClick={() => {
                try {
                  const value = JSON.parse(jsonText);
                  if (
                    !Array.isArray(value.objects) ||
                    !Array.isArray(value.relations) ||
                    !Array.isArray(value.metrics) ||
                    !Array.isArray(value.mapping_ids)
                  )
                    throw new Error("DSL 缺少对象、关系、指标或映射数组");
                  change(value);
                  setModal("");
                } catch (e) {
                  setError((e as Error).message);
                }
              }}
            >
              应用到草案
            </button>
          </div>
        </StudioModal>
      )}
      {modal === "revoke-identity" && (
        <StudioModal title="撤销身份决议" close={() => setModal("")}>
          <form onSubmit={(e) => {
            e.preventDefault(); action(async () => {
              await api(`/v1/identities/reviews/${identity.id}/revoke`, {reason: jsonText});
              await load(); setModal(""); setNotice("决议已撤销。请修正草案、重新校验并发布；历史版本仍保留原审批证据。");
            });
          }}>
            <p>撤销后可重新提交身份复核。发布新本体版本后，修正才会用于业务能力。</p>
            <label>撤销依据<textarea required minLength={3} value={jsonText} onChange={e => setJsonText(e.target.value)} /></label>
            <div className="modal-actions"><button className="primary" disabled={busy}>记录撤销</button></div>
          </form>
        </StudioModal>
      )}
      {modal === "identity" && (
        <StudioModal title="复核跨系统身份" close={() => setModal("")}>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              action(async () => {
                await api("/v1/identities/reviews", {
                  ontology_id: selected,
                  ...identity,
                });
                await load();
                setModal("");
              });
            }}
          >
            <p>
              选择需要合并的来源身份与权威目标。差异和原始来源会保留，独立审批通过后才能用于新版本。
            </p>
            <div className="form-grid">
              {[
                ["mapping_id", "source_key", "待合并来源"],
                ["target_mapping_id", "target_source_key", "权威目标"],
              ].map(([key, field, label]) => (
                <label key={key}>
                  {label}
                  <select
                    required
                    value={identity[key]}
                    onChange={(e) =>
                      setIdentity({ ...identity, [key]: e.target.value })
                    }
                  >
                    <option value="">选择批准映射</option>
                    {ownMappings
                      .filter((m) => m.status === "approved")
                      .map((m) => (
                        <option key={m.id} value={m.id}>
                          {m.object} · {m.id.slice(-8)}
                        </option>
                      ))}
                  </select>
                  <input
                    required
                    placeholder="来源记录主键"
                    value={identity[field]}
                    onChange={(e) =>
                      setIdentity({ ...identity, [field]: e.target.value })
                    }
                  />
                </label>
              ))}
            </div>
            <label>
              业务核对依据
              <textarea
                required
                minLength={5}
                value={identity.reason}
                onChange={(e) =>
                  setIdentity({ ...identity, reason: e.target.value })
                }
              />
            </label>
            <div className="modal-actions">
              <button className="primary" disabled={busy}>
                提交身份复核
              </button>
            </div>
          </form>
        </StudioModal>
      )}
      {modal === "rollback" && (
        <StudioModal
          title={`回滚到 v${relation.version}`}
          close={() => setModal("")}
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              action(async () => {
                await api(`/v1/ontologies/${selected}/rollback`, {
                  version: relation.version,
                  reason: jsonText,
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
                value={jsonText}
                onChange={(e) => setJsonText(e.target.value)}
              />
            </label>
            <div className="modal-actions">
              <button className="primary" disabled={busy}>
                确认版本回滚
              </button>
            </div>
          </form>
        </StudioModal>
      )}
    </div>
  );
}
