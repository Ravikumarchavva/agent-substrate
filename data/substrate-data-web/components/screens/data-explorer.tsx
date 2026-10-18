"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  ChevronRight,
  Database,
  Eye,
  Folder,
  Box,
  Plus,
  RefreshCw,
  ShieldCheck,
  Table2,
  Terminal,
  Upload,
} from "lucide-react";
import { useRef, useState } from "react";
import { TableMaintenance } from "../table-maintenance";
import {
  Bar,
  BarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { usePanelPreference } from "../../hooks/use-panel-preference";
import type { WorkspaceModel } from "../../hooks/use-workspace";
import {
  type Dataset,
  type CatalogAsset,
  type GrantAssignment,
  type StatementResponse,
  type ViewDef,
  resultFrom,
} from "../../lib/types";
import { bytes, number, time } from "../../lib/format";
import { api, isComputeUnavailable, post } from "../api";
import { SchemaAssets, CatalogAssetDetail } from "../catalog-assets";
import { Empty } from "../empty";
import { Grid } from "../grid";
import { PreviewCompute } from "../preview-compute";
import { Button } from "../ui/button";
import { SidebarToggleIcon } from "../ui/sidebar-toggle-icon";

type Tab = "Overview" | "Preview" | "Profile" | "Maintenance" | "Permissions";
type Selection = { catalog: string; schema?: string };

const PRIVILEGES = {
  table: ["SELECT", "MODIFY"],
  view: ["SELECT"],
  schema: [
    "USE_SCHEMA",
    "SELECT",
    "MODIFY",
    "CREATE_TABLE",
    "CREATE_VOLUME",
    "CREATE_MODEL",
    "READ_VOLUME",
    "WRITE_VOLUME",
    "USE_MODEL",
  ],
};
const TRANSFORMS = ["identity", "month", "day", "year", "hour"];
const tableName = (file: string) =>
  file
    .replace(/\.[^.]+$/, "")
    .toLowerCase()
    .replace(/[^a-z0-9_]/g, "_")
    .replace(/^([0-9])/, "t_$1")
    .slice(0, 64) || "upload";

export function ExplorerScreen({ model }: { model: WorkspaceModel }) {
  const client = useQueryClient();
  const {
    catalog,
    datasets,
    current,
    preview,
    profile,
    offset,
    setOffset,
    sort,
    setSort,
    descending,
    setDescending,
    setSelected,
    setSql,
    setSection,
    open,
    act,
    importFile,
    me,
    setNotice,
    busy,
  } = model;
  const [sidebarOpen, setSidebarOpen] = usePanelPreference(
    "catalog-open",
    true,
  );
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [selection, setSelection] = useState<Selection>({
    catalog: "main",
    schema: "default",
  });
  const [tab, setTab] = useState<Tab>("Overview");
  const [asset, setAsset] = useState<CatalogAsset | null>(null);
  const [assetCategory, setAssetCategory] = useState("tables");
  const [view, setView] = useState<ViewDef | null>(null);
  const [viewName, setViewName] = useState("");
  const [viewSql, setViewSql] = useState("");
  const [viewComment, setViewComment] = useState("");
  const [showViewForm, setShowViewForm] = useState(false);
  const [newSchema, setNewSchema] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadName, setUploadName] = useState("");
  const [partitionColumn, setPartitionColumn] = useState("");
  const [partitionTransform, setPartitionTransform] = useState("month");
  const chooser = useRef<HTMLInputElement>(null);

  const tree = catalog.data?.catalogs ?? [];
  const schema = tree
    .find((c) => c.name === selection.catalog)
    ?.schemas.find((s) => s.name === selection.schema);
  const target = `${selection.catalog}.${selection.schema}`;

  const grantTarget = view
    ? { type: "view", name: view.id }
    : current
      ? { type: "table", name: current.id }
      : selection.schema
        ? { type: "schema", name: target }
        : null;
  const grants = useQuery({
    queryKey: ["grants", grantTarget?.type, grantTarget?.name],
    queryFn: () =>
      api<{ privilege_assignments: GrantAssignment[] }>(
        `/grants/${grantTarget!.type}/${grantTarget!.name}`,
      ).then((r) => r.privilege_assignments),
    enabled: !!grantTarget && tab === "Permissions",
  });
  const viewPreview = useQuery({
    queryKey: ["view-preview", view?.id],
    queryFn: () => {
      const [c, s, v] = view!.id.split(".");
      return api<StatementResponse>(
        `/catalogs/${c}/schemas/${s}/views/${v}/preview?limit=100`,
      ).then(resultFrom);
    },
    enabled: !!view && tab === "Preview",
    retry: (count, failure) => !isComputeUnavailable(failure) && count < 1,
  });
  const [granteeKind, setGranteeKind] = useState("group");
  const [granteeName, setGranteeName] = useState("");
  const [privilege, setPrivilege] = useState("SELECT");

  async function changeGrant(
    principal: string,
    change: { add?: string[]; remove?: string[] },
  ) {
    await act("grants", async () => {
      await api(
        `/grants/${grantTarget!.type}/${grantTarget!.name}`,
        post(
          {
            changes: [
              { principal, add: change.add ?? [], remove: change.remove ?? [] },
            ],
          },
          "PATCH",
        ),
      );
      await client.invalidateQueries({ queryKey: ["grants"] });
    });
  }

  function chooseSchema(catalogName: string, schemaName?: string) {
    setAsset(null);
    setAssetCategory("tables");
    setSelection({ catalog: catalogName, schema: schemaName });
    setSelected(null);
    setView(null);
    setTab("Overview");
  }
  function openView(v: ViewDef) {
    setAsset(null);
    const [c, sch] = v.id.split(".");
    setSelection({ catalog: c, schema: sch });
    setSelected(null);
    setView(v);
    setTab("Overview");
  }
  function openTable(d: Dataset) {
    setAsset(null);
    setSelection({ catalog: d.catalog_name, schema: d.schema_name });
    setView(null);
    setTab("Overview");
    open(d);
  }
  const toggle = (key: string) =>
    setExpanded((prev) => ({ ...prev, [key]: !(prev[key] ?? true) }));
  const isOpen = (key: string) => expanded[key] ?? true;

  const term = model.search.trim().toLowerCase();
  if (term) {
    const results = tree
      .flatMap((c) =>
        c.schemas.flatMap((s) => [
          ...s.tables.map((d) => ({
            name: d.id,
            kind: "Table",
            owner: d.owner,
            open: () => openTable(d),
          })),
          ...s.views.map((v) => ({
            name: v.full_name,
            kind: "View",
            owner: v.owner,
            open: () => openView(v),
          })),
          ...[...s.volumes, ...s.models].map((item) => ({
            name: item.full_name,
            kind: item.kind === "volume" ? "Volume" : "Model",
            owner: item.owner,
            open: () => {
              setSelection({ catalog: c.name, schema: s.name });
              setSelected(null);
              setView(null);
              setAsset(item);
            },
          })),
        ]),
      )
      .filter((item) => item.name.toLowerCase().includes(term));
    return (
      <section aria-label="Catalog search results">
        <div className="catalog-section-heading">
          <div>
            <h2>Search results</h2>
            <p className="muted">
              {results.length} visible objects matching “{model.search}”
            </p>
          </div>
          <Button variant="secondary" onClick={() => model.setSearch("")}>
            Clear search
          </Button>
        </div>
        <table className="unity-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Type</th>
              <th>Owner</th>
            </tr>
          </thead>
          <tbody>
            {results.map((item) => (
              <tr key={`${item.kind}:${item.name}`}>
                <td>
                  <button
                    className="text-link"
                    onClick={() => {
                      model.setSearch("");
                      item.open();
                    }}
                  >
                    {item.name}
                  </button>
                </td>
                <td>{item.kind}</td>
                <td>{item.owner}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!results.length && (
          <Empty text="No matching catalog objects are visible to you." />
        )}
      </section>
    );
  }

  return (
    <div className={`catalog-layout ${sidebarOpen ? "" : "catalog-collapsed"}`}>
      {sidebarOpen ? (
        <aside className="catalog-browser" aria-label="Catalog">
          <div className="catalog-browser-heading">
            <span style={{ letterSpacing: "0.5px" }}>CATALOGS</span>
            <button
              className="panel-toggle-btn"
              aria-label="Collapse catalog sidebar"
              onClick={() => setSidebarOpen(false)}
            >
              <SidebarToggleIcon direction="close" width={16} height={16} />
            </button>
          </div>
          <div className="catalog-tree-body" style={{ padding: "0 4px" }}>
            {catalog.isPending && <Empty text="Loading catalogs…" />}
            {tree.map((c) => (
              <div className="tree-group" key={c.name}>
                <button
                  type="button"
                  className="tree-node"
                  onClick={() => toggle(c.name)}
                >
                  <ChevronRight
                    size={12}
                    className={`tree-arrow ${isOpen(c.name) ? "open" : ""}`}
                  />
                  <BookOpen size={13} className="icon-catalog" />
                  <span style={{ fontWeight: 500 }}>{c.name}</span>
                </button>
                {isOpen(c.name) && (
                  <div className="tree-children">
                    {c.schemas.map((s) => {
                      const key = `${c.name}.${s.name}`;
                      const chosen =
                        !current &&
                        selection.catalog === c.name &&
                        selection.schema === s.name;
                      return (
                        <div className="tree-group" key={key}>
                          <button
                            type="button"
                            className={`tree-node ${chosen ? "unity-selected" : ""}`}
                            onClick={() => {
                              chooseSchema(c.name, s.name);
                              toggle(key);
                            }}
                          >
                            <ChevronRight
                              size={12}
                              className={`tree-arrow ${isOpen(key) ? "open" : ""}`}
                            />
                            <Database size={13} className="icon-schema" />
                            <span>{s.name}</span>
                          </button>
                          {isOpen(key) && (
                            <div className="tree-children">
                              <details className="catalog-object-group" open>
                                <summary>
                                  <Table2 size={13} /> Tables ({s.tables.length}
                                  )
                                </summary>
                                {s.tables.map((d) => (
                                  <div
                                    key={d.id}
                                    className={`tree-table-item ${current?.id === d.id ? "unity-selected" : ""}`}
                                  >
                                    <div className="tree-table-row">
                                      <button
                                        type="button"
                                        className="tree-table-label"
                                        title={d.id}
                                        onClick={() => openTable(d)}
                                      >
                                        <Table2
                                          size={12}
                                          className="icon-table"
                                        />
                                        <span className="table-name-text">
                                          {d.display_name}
                                        </span>
                                      </button>
                                    </div>
                                  </div>
                                ))}
                              </details>
                              <details className="catalog-object-group" open>
                                <summary>
                                  <Eye size={13} /> Views ({s.views.length})
                                </summary>
                                {s.views.map((v) => (
                                  <div
                                    key={v.id}
                                    className={`tree-table-item ${view?.id === v.id ? "unity-selected" : ""}`}
                                  >
                                    <div className="tree-table-row">
                                      <button
                                        type="button"
                                        className="tree-table-label"
                                        title={`${v.id} (view)`}
                                        onClick={() => openView(v)}
                                      >
                                        <Eye size={12} className="icon-table" />
                                        <span className="table-name-text">
                                          {v.name}
                                        </span>
                                      </button>
                                    </div>
                                  </div>
                                ))}
                              </details>
                              {(["volumes", "models"] as const).map((kind) => (
                                <details
                                  className="catalog-object-group"
                                  key={kind}
                                >
                                  <summary>
                                    {kind === "volumes" ? (
                                      <Folder size={13} />
                                    ) : (
                                      <Box size={13} />
                                    )}{" "}
                                    {kind === "volumes" ? "Volumes" : "Models"}{" "}
                                    ({s[kind].length})
                                  </summary>
                                  {s[kind].map((item) => (
                                    <button
                                      className={`tree-table-label ${asset?.id === item.id ? "unity-selected" : ""}`}
                                      key={item.id}
                                      onClick={() => {
                                        setSelection({
                                          catalog: c.name,
                                          schema: s.name,
                                        });
                                        setSelected(null);
                                        setView(null);
                                        setAsset(item);
                                      }}
                                    >
                                      {kind === "volumes" ? (
                                        <Folder size={13} />
                                      ) : (
                                        <Box size={13} />
                                      )}{" "}
                                      {item.name}
                                    </button>
                                  ))}
                                  {!s[kind].length && (
                                    <button
                                      className="tree-empty text-link"
                                      onClick={() => {
                                        chooseSchema(c.name, s.name);
                                        setAssetCategory(kind);
                                      }}
                                    >
                                      Create{" "}
                                      {kind === "volumes"
                                        ? "volume"
                                        : "model registration"}
                                    </button>
                                  )}
                                </details>
                              ))}
                              {!s.tables.length && !s.views.length && (
                                <div className="tree-empty muted">
                                  No tables
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            ))}
            {catalog.isSuccess && !tree.length && (
              <Empty text="No catalogs are visible to you." />
            )}
          </div>
        </aside>
      ) : (
        <div className="catalog-rail">
          <button
            className="rail-catalog-btn"
            aria-label="Open catalog sidebar"
            onClick={() => setSidebarOpen(true)}
          >
            <SidebarToggleIcon direction="open" width={16} height={16} />
          </button>
        </div>
      )}

      <div className="catalog-content">
        <div className="unity-breadcrumbs">
          <button
            className="crumb-link"
            onClick={() => chooseSchema(selection.catalog)}
          >
            {selection.catalog}
          </button>
          {selection.schema && (
            <>
              <span className="crumb-separator">&gt;</span>
              <button
                className="crumb-link"
                onClick={() =>
                  chooseSchema(selection.catalog, selection.schema)
                }
              >
                {selection.schema}
              </button>
            </>
          )}
          {(current || view || asset) && (
            <>
              <span className="crumb-separator">&gt;</span>
              <span className="crumb-current">
                {current?.display_name ?? view?.name ?? asset?.name}
              </span>
            </>
          )}
        </div>

        {asset ? (
          <CatalogAssetDetail
            key={asset.id}
            asset={asset}
            onBack={() => setAsset(null)}
          />
        ) : view ? (
          <>
            <div className="unity-entity-header">
              <div className="unity-title-group">
                <Eye size={22} className="icon-table" />
                <h1 className="unity-entity-title">{view.id}</h1>
                <span className="unity-badge unity-badge-schema">VIEW</span>
              </div>
              <div className="unity-header-actions">
                <Button
                  variant="secondary"
                  onClick={() => {
                    setSql(`SELECT *\nFROM ${view.id}\nLIMIT 100;`);
                    setSection("SQL workspace");
                  }}
                >
                  <Terminal size={14} /> Query
                </Button>
                <Button
                  variant="secondary"
                  disabled={!!busy}
                  onClick={() =>
                    void act("view", async () => {
                      const [c, sch, v] = view.id.split(".");
                      await api(`/catalogs/${c}/schemas/${sch}/views/${v}`, {
                        method: "DELETE",
                      });
                      setView(null);
                      await datasets.refetch();
                      setNotice(`Dropped view ${view.id}.`);
                    })
                  }
                >
                  Drop view
                </Button>
              </div>
            </div>
            <div className="unity-tabs-bar">
              {(["Overview", "Preview", "Permissions"] as Tab[]).map((name) => (
                <button
                  key={name}
                  className={`unity-tab-btn ${tab === name ? "active" : ""}`}
                  onClick={() => setTab(name)}
                >
                  {name}
                </button>
              ))}
            </div>
            {tab === "Overview" && (
              <div className="unity-card">
                <div className="unity-details-grid">
                  <div className="unity-detail-label">Owner</div>
                  <div className="unity-detail-value">{view.owner}</div>
                  <div className="unity-detail-label">Comment</div>
                  <div className="unity-detail-value">
                    {view.comment ?? "—"}
                  </div>
                  <div className="unity-detail-label">Created</div>
                  <div className="unity-detail-value">
                    {time(view.created_at)}
                  </div>
                </div>
                <p className="muted">
                  Readers need SELECT on this view only. The view reads its
                  tables with its owner&apos;s access.
                </p>
                <pre className="code-block">{view.sql}</pre>
                <table className="unity-table" style={{ marginTop: 16 }}>
                  <thead>
                    <tr>
                      <th>COLUMN</th>
                      <th>TYPE</th>
                    </tr>
                  </thead>
                  <tbody>
                    {view.columns.map((column) => (
                      <tr key={column.name}>
                        <td style={{ fontWeight: 500 }}>{column.name}</td>
                        <td>
                          <code>{column.type}</code>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {tab === "Preview" && (
              <section className="panel detail-panel">
                {isComputeUnavailable(viewPreview.error) ? (
                  <PreviewCompute
                    key={view.id}
                    error={viewPreview.error as Error}
                  />
                ) : viewPreview.isFetching ? (
                  <Empty text="Running the view…" />
                ) : viewPreview.isError ? (
                  <p role="alert">{viewPreview.error.message}</p>
                ) : (
                  viewPreview.data && (
                    <>
                      <Grid
                        key={view?.id}
                        columns={viewPreview.data.columns}
                        rows={viewPreview.data.rows}
                      />
                      <div className="pagination">
                        <span>
                          {viewPreview.data.row_count} rows ·{" "}
                          {viewPreview.data.files_scanned} files ·{" "}
                          {viewPreview.data.duration_ms} ms
                        </span>
                      </div>
                    </>
                  )
                )}
              </section>
            )}
            {tab === "Permissions" && permissions()}
          </>
        ) : current ? (
          <>
            <div className="unity-entity-header">
              <div className="unity-title-group">
                <Table2 size={22} className="icon-table" />
                <h1 className="unity-entity-title">{current.id}</h1>
              </div>
              <div className="unity-header-actions">
                <Button
                  variant="secondary"
                  onClick={() => {
                    setSql(`SELECT *\nFROM ${current.id}\nLIMIT 100;`);
                    setSection("SQL workspace");
                  }}
                >
                  <Terminal size={14} /> Query
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => void datasets.refetch()}
                  aria-label="Refresh"
                >
                  <RefreshCw size={14} />
                </Button>
              </div>
            </div>
            <div className="unity-tabs-bar">
              {(
                [
                  "Overview",
                  "Preview",
                  "Profile",
                  "Maintenance",
                  "Permissions",
                ] as Tab[]
              ).map((name) => (
                <button
                  key={name}
                  className={`unity-tab-btn ${tab === name ? "active" : ""}`}
                  onClick={() => setTab(name)}
                >
                  {name}
                </button>
              ))}
            </div>

            {tab === "Overview" && (
              <div className="unity-card">
                <div className="unity-details-grid">
                  <div className="unity-detail-label">Rows</div>
                  <div className="unity-detail-value">
                    {number(current.row_count)}
                  </div>
                  <div className="unity-detail-label">Size</div>
                  <div className="unity-detail-value">
                    {bytes(current.size_bytes)} in {current.file_count} data
                    files
                  </div>
                  <div className="unity-detail-label">Partitioning</div>
                  <div className="unity-detail-value">
                    {current.partitioning.length
                      ? current.partitioning
                          .map((p) => `${p.transform}(${p.column})`)
                          .join(", ")
                      : "None"}
                  </div>
                  <div className="unity-detail-label">Owner</div>
                  <div className="unity-detail-value">{current.owner}</div>
                  <div className="unity-detail-label">Snapshot</div>
                  <div className="unity-detail-value">
                    <code>{current.snapshot_id ?? "empty"}</code>
                  </div>
                  <div className="unity-detail-label">Updated</div>
                  <div className="unity-detail-value">
                    {time(current.updated_at)}
                  </div>
                </div>
                <table className="unity-table" style={{ marginTop: 16 }}>
                  <thead>
                    <tr>
                      <th>COLUMN</th>
                      <th>TYPE</th>
                    </tr>
                  </thead>
                  <tbody>
                    {current.columns.map((column) => (
                      <tr key={column.name}>
                        <td style={{ fontWeight: 500 }}>{column.name}</td>
                        <td>
                          <code>{column.dtype}</code>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {tab === "Preview" && (
              <section className="panel detail-panel">
                {isComputeUnavailable(preview.error) ? (
                  <PreviewCompute
                    key={current.id}
                    error={preview.error as Error}
                  />
                ) : preview.isFetching ? (
                  <Empty text="Reading preview…" />
                ) : (
                  preview.data && (
                    <Grid
                      key={`${current.id}:${offset}`}
                      columns={preview.data.columns}
                      rows={preview.data.rows}
                      sortBy={{ column: sort, descending }}
                      onSort={(name) => {
                        setSort(name);
                        setDescending(sort === name ? !descending : false);
                        setOffset(0);
                      }}
                    />
                  )
                )}
                <div className="pagination">
                  <span>
                    Rows {current.row_count ? offset + 1 : 0}–
                    {Math.min(offset + 100, current.row_count)} of{" "}
                    {current.row_count.toLocaleString()}
                    {preview.data?.files_scanned != null &&
                      ` · ${preview.data.files_scanned} files`}
                    {preview.data && ` · ${preview.data.duration_ms} ms`}
                  </span>
                  <div>
                    <Button
                      variant="secondary"
                      disabled={offset === 0}
                      onClick={() => setOffset(Math.max(0, offset - 100))}
                    >
                      <ArrowLeft size={14} /> Previous
                    </Button>
                    <Button
                      variant="secondary"
                      disabled={offset + 100 >= current.row_count}
                      onClick={() => setOffset(offset + 100)}
                    >
                      Next <ArrowRight size={14} />
                    </Button>
                  </div>
                </div>
              </section>
            )}

            {tab === "Profile" && (
              <section className="panel detail-panel">
                {profile.isFetching ? (
                  <Empty text="Computing profile on attached compute…" />
                ) : (
                  profile.data && (
                    <>
                      <div className="profile-summary">
                        <h2>
                          {profile.data.rows_examined.toLocaleString()} rows
                          examined
                        </h2>
                        <p>
                          {profile.data.sampled
                            ? `Sampled: ${profile.data.sampling}.`
                            : "All rows examined."}
                        </p>
                      </div>
                      <div className="chart">
                        <ResponsiveContainer width="100%" height={200}>
                          <BarChart data={profile.data.columns}>
                            <XAxis
                              dataKey="name"
                              stroke="var(--muted)"
                              fontSize={11}
                            />
                            <YAxis stroke="#8d95a6" fontSize={11} />
                            <Tooltip
                              contentStyle={{
                                background: "var(--surface)",
                                borderColor: "var(--border)",
                              }}
                            />
                            <Bar
                              name="Null percentage"
                              dataKey="null_percent"
                              fill="var(--violet)"
                              radius={[3, 3, 0, 0]}
                            />
                          </BarChart>
                        </ResponsiveContainer>
                      </div>
                      <div className="table-overflow">
                        <table className="standard-table">
                          <thead>
                            <tr>
                              {[
                                "Column",
                                "Nulls",
                                "Null %",
                                "Distinct (approx)",
                                "Min",
                                "Max",
                                "Mean",
                                "Std",
                                "Length",
                              ].map((h) => (
                                <th key={h}>{h}</th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {profile.data.columns.map((c) => (
                              <tr key={c.name}>
                                <td>{c.name}</td>
                                <td>{c.nulls}</td>
                                <td>{c.null_percent}%</td>
                                <td>{c.distinct}</td>
                                <td>{c.min == null ? "—" : String(c.min)}</td>
                                <td>{c.max == null ? "—" : String(c.max)}</td>
                                <td>{c.mean?.toFixed(2) ?? "—"}</td>
                                <td>{c.std?.toFixed(2) ?? "—"}</td>
                                <td>
                                  {c.min_length != null
                                    ? `${c.min_length}–${c.max_length}`
                                    : "—"}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </>
                  )
                )}
              </section>
            )}

            {tab === "Maintenance" && (
              <TableMaintenance
                dataset={current}
                isOwner={
                  current.owner === me.data?.principal || !!me.data?.is_admin
                }
              />
            )}
            {tab === "Permissions" && permissions()}
          </>
        ) : selection.schema ? (
          <>
            <div className="unity-entity-header">
              <div className="unity-title-group">
                <Database size={22} className="icon-schema" />
                <h1 className="unity-entity-title">{target}</h1>
                <span className="unity-badge unity-badge-schema">SCHEMA</span>
              </div>
              <div className="unity-header-actions">
                <Button
                  variant="secondary"
                  onClick={() =>
                    setTab(tab === "Permissions" ? "Overview" : "Permissions")
                  }
                >
                  <ShieldCheck size={14} /> Permissions
                </Button>
                <Button
                  variant="secondary"
                  onClick={() => chooser.current?.click()}
                  disabled={!!busy}
                >
                  <Upload size={14} /> Upload table
                </Button>
                <input
                  ref={chooser}
                  type="file"
                  hidden
                  accept=".csv,.parquet,.ndjson,.jsonl"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) {
                      setUploadFile(file);
                      setUploadName(tableName(file.name));
                    }
                    event.target.value = "";
                  }}
                />
              </div>
            </div>

            <nav className="catalog-section-tabs" aria-label="Schema objects">
              {(["tables", "views", "volumes", "models"] as const).map(
                (kind) => (
                  <button
                    key={kind}
                    disabled={
                      (kind === "volumes" || kind === "models") &&
                      catalog.data?.assetsAvailable === false
                    }
                    aria-current={assetCategory === kind ? "page" : undefined}
                    onClick={() => {
                      setAssetCategory(kind);
                      setTab("Overview");
                    }}
                  >
                    {kind[0].toUpperCase() + kind.slice(1)}{" "}
                    <span>{schema?.[kind].length ?? 0}</span>
                  </button>
                ),
              )}
            </nav>
            {tab !== "Permissions" &&
              (assetCategory === "volumes" || assetCategory === "models") && (
                <SchemaAssets
                  key={target + assetCategory}
                  catalog={selection.catalog}
                  schema={selection.schema}
                  kind={assetCategory}
                  items={schema?.[assetCategory] ?? []}
                  onSelect={setAsset}
                  onCreated={() => datasets.refetch()}
                />
              )}
            {uploadFile && (
              <form
                className="unity-card"
                onSubmit={(event) => {
                  event.preventDefault();
                  const partitioning = partitionColumn.trim()
                    ? [
                        {
                          column: partitionColumn.trim(),
                          transform: partitionTransform,
                        },
                      ]
                    : [];
                  void importFile(
                    uploadFile,
                    `${target}.${uploadName}`,
                    partitioning,
                  );
                  setUploadFile(null);
                  setPartitionColumn("");
                }}
              >
                <h3 className="unity-card-title">
                  Upload {uploadFile.name} ({bytes(uploadFile.size)}) to{" "}
                  {target}
                </h3>
                <p className="muted">
                  The file goes straight to object storage; attached compute
                  converts it once into a partitioned table.
                </p>
                <div
                  style={{
                    display: "flex",
                    gap: 8,
                    flexWrap: "wrap",
                    alignItems: "center",
                  }}
                >
                  <label>
                    Table name
                    <input
                      required
                      pattern="[a-z_][a-z0-9_]{0,63}"
                      value={uploadName}
                      onChange={(e) => setUploadName(e.target.value)}
                    />
                  </label>
                  <label>
                    Partition by (optional)
                    <input
                      placeholder="column, e.g. pickup_time"
                      value={partitionColumn}
                      onChange={(e) => setPartitionColumn(e.target.value)}
                    />
                  </label>
                  <label>
                    Transform
                    <select
                      value={partitionTransform}
                      onChange={(e) => setPartitionTransform(e.target.value)}
                    >
                      {TRANSFORMS.map((t) => (
                        <option key={t}>{t}</option>
                      ))}
                    </select>
                  </label>
                  <Button disabled={!!busy}>
                    <Upload size={14} /> Upload & ingest
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    onClick={() => setUploadFile(null)}
                  >
                    Cancel
                  </Button>
                </div>
              </form>
            )}

            {tab === "Permissions" ? (
              permissions()
            ) : assetCategory === "tables" ? (
              <div className="unity-card">
                <h3 className="unity-card-title">Tables</h3>
                <table className="unity-table">
                  <thead>
                    <tr>
                      <th>TABLE</th>
                      <th>ROWS</th>
                      <th>SIZE</th>
                      <th>PARTITIONING</th>
                      <th>OWNER</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(schema?.tables ?? []).map((d) => (
                      <tr
                        key={d.id}
                        onClick={() => openTable(d)}
                        style={{ cursor: "pointer" }}
                      >
                        <td style={{ fontWeight: 500 }}>{d.display_name}</td>
                        <td>{number(d.row_count)}</td>
                        <td>{bytes(d.size_bytes)}</td>
                        <td>
                          {d.partitioning.length
                            ? d.partitioning
                                .map((p) => `${p.transform}(${p.column})`)
                                .join(", ")
                            : "—"}
                        </td>
                        <td>{d.owner}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!schema?.tables.length && (
                  <Empty text="No tables yet. Upload a CSV, Parquet or NDJSON file." />
                )}
              </div>
            ) : null}
            {tab !== "Permissions" && assetCategory === "views" && (
              <div className="unity-card">
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                  }}
                >
                  <h3 className="unity-card-title">Views</h3>
                  <Button
                    variant="secondary"
                    onClick={() => setShowViewForm((open) => !open)}
                  >
                    <Plus size={14} /> New view
                  </Button>
                </div>
                {showViewForm && (
                  <form
                    style={{ display: "grid", gap: 8, margin: "12px 0" }}
                    onSubmit={(event) => {
                      event.preventDefault();
                      void act("view", async () => {
                        await api(
                          `/catalogs/${selection.catalog}/schemas/${selection.schema}/views`,
                          post({
                            name: viewName,
                            sql: viewSql,
                            comment: viewComment || null,
                          }),
                        );
                        setShowViewForm(false);
                        setViewName("");
                        setViewSql("");
                        setViewComment("");
                        await datasets.refetch();
                        setNotice(`Created view ${target}.${viewName}.`);
                      });
                    }}
                  >
                    <input
                      required
                      aria-label="View name"
                      pattern="[a-z_][a-z0-9_]{0,63}"
                      placeholder="view_name"
                      value={viewName}
                      onChange={(e) => setViewName(e.target.value)}
                    />
                    <textarea
                      required
                      aria-label="View query"
                      rows={5}
                      placeholder={`SELECT region, count(*) AS orders FROM ${target}.orders GROUP BY region`}
                      value={viewSql}
                      onChange={(e) => setViewSql(e.target.value)}
                    />
                    <input
                      aria-label="View comment"
                      placeholder="Comment (optional)"
                      value={viewComment}
                      onChange={(e) => setViewComment(e.target.value)}
                    />
                    <div>
                      <Button disabled={!!busy}>Create view</Button>
                    </div>
                  </form>
                )}
                <table className="unity-table">
                  <thead>
                    <tr>
                      <th>VIEW</th>
                      <th>COLUMNS</th>
                      <th>OWNER</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(schema?.views ?? []).map((v) => (
                      <tr
                        key={v.id}
                        onClick={() => openView(v)}
                        style={{ cursor: "pointer" }}
                      >
                        <td style={{ fontWeight: 500 }}>{v.name}</td>
                        <td>{v.columns.map((c) => c.name).join(", ")}</td>
                        <td>{v.owner}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!schema?.views.length && !showViewForm && (
                  <Empty text="No views. A view is a named, governed query." />
                )}
              </div>
            )}
          </>
        ) : (
          <>
            <div className="unity-entity-header">
              <div className="unity-title-group">
                <BookOpen size={22} className="icon-catalog" />
                <h1 className="unity-entity-title">{selection.catalog}</h1>
                <span className="unity-badge unity-badge-schema">CATALOG</span>
              </div>
            </div>
            <div className="unity-card">
              <h3 className="unity-card-title">Schemas</h3>
              <table className="unity-table">
                <thead>
                  <tr>
                    <th>SCHEMA</th>
                    <th>TABLES</th>
                    <th>OWNER</th>
                  </tr>
                </thead>
                <tbody>
                  {(
                    tree.find((c) => c.name === selection.catalog)?.schemas ??
                    []
                  ).map((s) => (
                    <tr
                      key={s.name}
                      onClick={() => chooseSchema(selection.catalog, s.name)}
                      style={{ cursor: "pointer" }}
                    >
                      <td style={{ fontWeight: 500 }}>{s.name}</td>
                      <td>{s.tables.length}</td>
                      <td>{s.owner}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <form
                style={{ display: "flex", gap: 8, marginTop: 12 }}
                onSubmit={(event) => {
                  event.preventDefault();
                  void act("schema", async () => {
                    await api(
                      `/catalogs/${selection.catalog}/schemas`,
                      post({ name: newSchema }),
                    );
                    setNewSchema("");
                    await datasets.refetch();
                    setNotice(
                      `Created schema ${selection.catalog}.${newSchema}.`,
                    );
                  });
                }}
              >
                <input
                  aria-label="New schema name"
                  required
                  pattern="[a-z_][a-z0-9_]{0,63}"
                  placeholder="new_schema"
                  value={newSchema}
                  onChange={(e) => setNewSchema(e.target.value)}
                />
                <Button disabled={!!busy}>
                  <Plus size={14} /> Create schema
                </Button>
              </form>
            </div>
          </>
        )}
      </div>
    </div>
  );

  // A render helper, not a component: its inputs must keep focus across re-renders.
  function permissions() {
    const options =
      grantTarget?.type === "view"
        ? PRIVILEGES.view
        : grantTarget?.type === "table"
          ? PRIVILEGES.table
          : PRIVILEGES.schema;
    return (
      <div className="unity-card">
        <h3 className="unity-card-title">
          Permissions{" "}
          <span style={{ fontWeight: 400, color: "var(--muted)" }}>
            {grantTarget?.type} · {grantTarget?.name}
          </span>
        </h3>
        <p className="muted">
          Privileges inherit down the catalog, schema and table chain. Browsing
          requires USE_CATALOG and USE_SCHEMA on every parent.
        </p>
        {grants.isError && <p role="alert">{grants.error.message}</p>}
        <table className="unity-table">
          <thead>
            <tr>
              <th>PRINCIPAL</th>
              <th>PRIVILEGES</th>
            </tr>
          </thead>
          <tbody>
            {(grants.data ?? []).map((assignment) => (
              <tr key={assignment.principal}>
                <td style={{ fontWeight: 500 }}>{assignment.principal}</td>
                <td>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    {assignment.privileges.map((p) => (
                      <button
                        key={p}
                        type="button"
                        className="unity-badge unity-badge-managed"
                        title="Click to revoke"
                        onClick={() =>
                          void changeGrant(assignment.principal, {
                            remove: [p],
                          })
                        }
                      >
                        {p} ×
                      </button>
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!grants.data?.length && !grants.isPending && (
          <Empty text="No explicit grants; only the owner and admins have access." />
        )}
        <form
          style={{
            display: "flex",
            gap: 8,
            alignItems: "center",
            marginTop: 16,
            flexWrap: "wrap",
          }}
          onSubmit={(event) => {
            event.preventDefault();
            void changeGrant(`${granteeKind}:${granteeName.trim()}`, {
              add: [privilege],
            });
            setGranteeName("");
          }}
        >
          <select
            value={granteeKind}
            onChange={(e) => setGranteeKind(e.target.value)}
            aria-label="Principal type"
          >
            <option value="group">group</option>
            <option value="user">user</option>
            <option value="service">service</option>
          </select>
          <input
            required
            aria-label="Principal name"
            placeholder="analysts or alice"
            value={granteeName}
            onChange={(e) => setGranteeName(e.target.value)}
          />
          <select
            value={privilege}
            onChange={(e) => setPrivilege(e.target.value)}
            aria-label="Privilege"
          >
            {options.map((p) => (
              <option key={p}>{p}</option>
            ))}
          </select>
          <Button disabled={!!busy}>Grant</Button>
        </form>
      </div>
    );
  }
}
