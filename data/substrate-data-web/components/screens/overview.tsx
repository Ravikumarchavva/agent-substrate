"use client";

import {
  ArrowRight,
  ArrowUpRight,
  Clock,
  Database,
  GitBranch,
  Play,
  Plus,
  Search,
  Terminal,
  Workflow,
} from "lucide-react";
import { useState } from "react";
import type { WorkspaceModel } from "../../hooks/use-workspace";
import { bytes, datasetLabel, number, time } from "../../lib/format";
import type { Dataset } from "../../lib/types";
import { Button } from "../ui/button";

type WorkspaceTab = "recents" | "tables" | "queries" | "pipelines";

export function OverviewScreen({ model }: { model: WorkspaceModel }) {
  const {
    setSection,
    setSelected,
    setSql,
    datasets,
    overview,
    open,
    queryTable,
    fileInput,
    busy,
  } = model;

  const [activeTab, setActiveTab] = useState<WorkspaceTab>("recents");
  const [filter, setFilter] = useState("");

  const tableItems: Dataset[] = datasets.data || [];
  const queryItems = overview.data?.queries || [];
  const pipelineItems = overview.data?.runs || [];

  // Build unified recents list
  const recentAssets = [
    ...tableItems.slice(0, 8).map((d) => ({
      id: `table-${d.id}`,
      type: "table" as const,
      name: datasetLabel(d),
      secondary: `${d.columns?.length ?? 0} columns · ${d.format}`,
      metric: `${number(d.row_count)} rows · ${bytes(d.size_bytes)}`,
      action: () => open(d),
      queryAction: () => queryTable(d),
      raw: d,
    })),
    ...queryItems.slice(0, 6).map((q) => ({
      id: `query-${q.id}`,
      type: "query" as const,
      name: q.sql,
      secondary: `${q.duration_ms} ms · ${q.status}`,
      metric: "SQL Query",
      action: () => {
        setSql(q.sql);
        setSection("SQL workspace");
      },
      raw: q,
    })),
    ...pipelineItems.slice(0, 6).map((r) => ({
      id: `pipeline-${r.id}`,
      type: "pipeline" as const,
      name: r.name,
      secondary: `${r.status} · ${r.duration_ms ?? "—"} ms`,
      metric: "Pipeline Job",
      action: () => {
        if (r.output_id) {
          const d = tableItems.find((t) => t.id === r.output_id);
          if (d) {
            open(d);
            return;
          }
        }
        setSection("Pipelines");
      },
      raw: r,
    })),
  ];

  const filteredRecents = recentAssets.filter((item) =>
    item.name.toLowerCase().includes(filter.toLowerCase()),
  );

  const filteredTables = tableItems.filter((d) =>
    datasetLabel(d).toLowerCase().includes(filter.toLowerCase()),
  );

  const filteredQueries = queryItems.filter((q) =>
    q.sql.toLowerCase().includes(filter.toLowerCase()),
  );

  const filteredPipelines = pipelineItems.filter((p) =>
    p.name.toLowerCase().includes(filter.toLowerCase()),
  );

  return (
    <div className="workspace-view">
      <div className="workspace-tabs-bar">
        <div className="databricks-tabs">
          <button
            type="button"
            className={`databricks-tab ${activeTab === "recents" ? "active" : ""}`}
            onClick={() => setActiveTab("recents")}
          >
            Recents
          </button>
          <button
            type="button"
            className={`databricks-tab ${activeTab === "tables" ? "active" : ""}`}
            onClick={() => setActiveTab("tables")}
          >
            Catalog tables
            <span className="tab-count">{tableItems.length}</span>
          </button>
          <button
            type="button"
            className={`databricks-tab ${activeTab === "queries" ? "active" : ""}`}
            onClick={() => setActiveTab("queries")}
          >
            SQL queries
            <span className="tab-count">{queryItems.length}</span>
          </button>
          <button
            type="button"
            className={`databricks-tab ${activeTab === "pipelines" ? "active" : ""}`}
            onClick={() => setActiveTab("pipelines")}
          >
            Pipeline runs
            <span className="tab-count">{pipelineItems.length}</span>
          </button>
        </div>
      </div>

      <div className="compute-filter-bar">
        <label className="compute-search-input">
          <Search size={14} />
          <input
            type="text"
            placeholder="Filter workspace assets…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </label>
        <div className="compute-actions-right">
          <Button
            variant="secondary"
            onClick={() => {
              const defaultTable = datasets.data?.[0]?.name || "nyctaxi_trips";
              setSql(`SELECT * FROM ${defaultTable} LIMIT 10;`);
              setSection("SQL workspace");
            }}
          >
            <Terminal size={14} />
            New query
          </Button>
        </div>
      </div>

      {activeTab === "recents" && (
        <div className="table-overflow">
          <table className="standard-table databricks-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Type</th>
                <th>Details</th>
                <th>Metric</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {filteredRecents.map((item) => (
                <tr key={item.id}>
                  <td>
                    <button
                      type="button"
                      className="dataset-link"
                      onClick={item.action}
                    >
                      <div className="table-icon">
                        {item.type === "table" ? (
                          <Database size={15} />
                        ) : item.type === "query" ? (
                          <Terminal size={15} />
                        ) : (
                          <GitBranch size={15} />
                        )}
                      </div>
                      <span>
                        <strong className="table-row-title">{item.name}</strong>
                      </span>
                    </button>
                  </td>
                  <td>
                    <span className="badge">
                      {item.type === "table"
                        ? "Table"
                        : item.type === "query"
                          ? "Query"
                          : "Pipeline"}
                    </span>
                  </td>
                  <td className="muted">{item.secondary}</td>
                  <td className="muted">{item.metric}</td>
                  <td style={{ textAlign: "right" }}>
                    <button
                      type="button"
                      className="icon-button"
                      title="Open asset"
                      onClick={item.action}
                    >
                      <ArrowUpRight size={15} />
                    </button>
                  </td>
                </tr>
              ))}
              {filteredRecents.length === 0 && (
                <tr>
                  <td colSpan={5} className="empty-table-cell">
                    No workspace assets found.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {activeTab === "tables" && (
        <div className="table-overflow">
          <table className="standard-table databricks-table">
            <thead>
              <tr>
                <th>Dataset</th>
                <th>Format</th>
                <th>Rows</th>
                <th>Size</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {filteredTables.map((d) => (
                <tr key={d.id}>
                  <td>
                    <button
                      type="button"
                      className="dataset-link"
                      onClick={() => open(d)}
                    >
                      <div className="table-icon">
                        <Database size={15} />
                      </div>
                      <span>
                        <strong className="table-row-title">
                          {datasetLabel(d)}
                        </strong>
                        <small>
                          {d.columns?.length ?? 0} columns · {d.source_format}
                        </small>
                      </span>
                    </button>
                  </td>
                  <td>
                    <span className="badge">{d.format}</span>
                  </td>
                  <td>{number(d.row_count)}</td>
                  <td className="muted">{bytes(d.size_bytes)}</td>
                  <td style={{ textAlign: "right" }}>
                    <button
                      type="button"
                      className="icon-button"
                      title={`Query ${datasetLabel(d)}`}
                      onClick={() => queryTable(d)}
                    >
                      <ArrowUpRight size={15} />
                    </button>
                  </td>
                </tr>
              ))}
              {filteredTables.length === 0 && (
                <tr>
                  <td colSpan={5} className="empty-table-cell">
                    No tables registered in this workspace catalog.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {activeTab === "queries" && (
        <div className="table-overflow">
          <table className="standard-table databricks-table">
            <thead>
              <tr>
                <th>Query</th>
                <th>Status</th>
                <th>Duration</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {filteredQueries.map((q) => (
                <tr key={q.id}>
                  <td>
                    <button
                      type="button"
                      className="dataset-link"
                      onClick={() => {
                        setSql(q.sql);
                        setSection("SQL workspace");
                      }}
                    >
                      <div className="table-icon">
                        <Terminal size={15} />
                      </div>
                      <code className="sql-snippet">{q.sql}</code>
                    </button>
                  </td>
                  <td>
                    <span
                      className={`badge ${q.status === "failed" ? "badge-danger" : ""}`}
                    >
                      {q.status}
                    </span>
                  </td>
                  <td className="muted">{q.duration_ms} ms</td>
                  <td style={{ textAlign: "right" }}>
                    <button
                      type="button"
                      className="icon-button"
                      title="Open query in editor"
                      onClick={() => {
                        setSql(q.sql);
                        setSection("SQL workspace");
                      }}
                    >
                      <ArrowUpRight size={15} />
                    </button>
                  </td>
                </tr>
              ))}
              {filteredQueries.length === 0 && (
                <tr>
                  <td colSpan={4} className="empty-table-cell">
                    No recent SQL queries executed in this project.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {activeTab === "pipelines" && (
        <div className="table-overflow">
          <table className="standard-table databricks-table">
            <thead>
              <tr>
                <th>Job name</th>
                <th>Status</th>
                <th>Duration</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {filteredPipelines.map((r) => (
                <tr key={r.id}>
                  <td>
                    <button
                      type="button"
                      className="dataset-link"
                      onClick={() => setSection("Pipelines")}
                    >
                      <div className="table-icon">
                        <GitBranch size={15} />
                      </div>
                      <span>
                        <strong className="table-row-title">{r.name}</strong>
                      </span>
                    </button>
                  </td>
                  <td>
                    <span
                      className={`badge ${r.status === "failed" ? "badge-danger" : ""}`}
                    >
                      {r.status}
                    </span>
                  </td>
                  <td className="muted">{r.duration_ms ?? "—"} ms</td>
                  <td style={{ textAlign: "right" }}>
                    {r.output_id ? (
                      <button
                        type="button"
                        className="text-link"
                        onClick={() => {
                          const d = tableItems.find(
                            (t) => t.id === r.output_id,
                          );
                          if (d) open(d);
                        }}
                      >
                        View output <ArrowRight size={13} />
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="icon-button"
                        title="View pipeline"
                        onClick={() => setSection("Pipelines")}
                      >
                        <ArrowUpRight size={15} />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {filteredPipelines.length === 0 && (
                <tr>
                  <td colSpan={4} className="empty-table-cell">
                    No pipeline runs recorded in this workspace.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
