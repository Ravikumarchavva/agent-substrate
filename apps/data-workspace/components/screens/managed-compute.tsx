"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  Check,
  CheckCircle2,
  ChevronDown,
  Clock,
  ExternalLink,
  PauseCircle,
  Play,
  Plus,
  RefreshCw,
  Search,
  Server,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { ComputeApplicationAccess } from "../compute-application-access";
import { api, post } from "../api";

const ATTACH_FIELDS = [
  "name",
  "role",
  "url",
  "token",
  "concurrency",
  "max_queue",
];
const attachable = (value: Record<string, unknown>) =>
  Object.fromEntries(
    Object.entries(value).filter(([key]) => ATTACH_FIELDS.includes(key)),
  );
import { Button } from "../ui/button";

export type ComputeResource = {
  id: string;
  name: string;
  role: string;
  kind?: string;
  url: string | null;
  checked: number | null;
  state: string;
  desired: string;
  concurrency: number;
  max_queue: number;
  owner: string;
  project: string;
  last_error: string | null;
  endpoint: string | null;
};

type Job = {
  id: string;
  operation: string;
  state: string;
  attempts: number;
  max_attempts: number;
  error: string | null;
  resource_id: string;
  created: number;
  cancel: number;
};

const kindOf = (resource: ComputeResource) =>
  resource.kind ??
  ({
    sql: "sql_warehouse",
    jobs: "all_purpose",
    rag: "rag",
    inference: "inference",
  }[resource.role] ||
    resource.role);

const kindLabels: Record<string, string> = {
  sql_warehouse: "SQL warehouse",
  all_purpose: "All-purpose compute",
  rag: "AI Search / RAG",
  inference: "Model inference",
};

// The application never starts or stops compute. These actions only resume or pause
// routing of work to an endpoint that its operator runs.
const actionOf = (resource: ComputeResource) =>
  resource.desired === "running" ? "stop" : "start";

const actionLabel = (resource: ComputeResource) =>
  actionOf(resource) === "start" ? "Resume routing" : "Pause routing";

const stateLabel = (resource: ComputeResource) =>
  ({
    ready: "Attached · ready",
    starting: "Connecting…",
    error: "Attached · unreachable",
    stopped: "Paused",
  })[resource.state] ?? resource.state;

export function ResourcePicker({
  role,
  value,
  onChange,
}: {
  role: string;
  kind?: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const resources = useQuery({
    queryKey: ["compute-resources"],
    queryFn: () =>
      api<{ resources: ComputeResource[] }>("/compute/resources").then(
        (r) => r.resources,
      ),
    retry: false,
  });

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    if (open) {
      document.addEventListener("mousedown", handleClickOutside);
      return () =>
        document.removeEventListener("mousedown", handleClickOutside);
    }
  }, [open]);

  if (!resources.data) return null;

  const filtered = resources.data.filter(
    (resource) =>
      resource.role === role ||
      (role === "sql" && kindOf(resource) === "all_purpose"),
  );

  const selected = filtered.find((r) => r.id === value);
  const defaultLabel =
    role === "sql" ? "Any attached SQL warehouse" : "Any attached compute";
  const displayLabel = selected ? selected.name : defaultLabel;

  return (
    <div className="databricks-dropdown-wrapper" ref={ref}>
      <button
        type="button"
        className="databricks-dropdown-trigger"
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => setOpen(!open)}
      >
        <span className={`status-dot ${selected ? selected.state : "ready"}`} />
        <span className="trigger-label">{displayLabel}</span>
        <ChevronDown
          size={12}
          className={`trigger-chevron ${open ? "open" : ""}`}
        />
      </button>

      {open && (
        <div className="databricks-dropdown-menu panel" role="listbox">
          <div className="dropdown-menu-header">
            {role === "sql" ? "SQL WAREHOUSES" : "COMPUTE ENDPOINTS"}
          </div>
          <button
            type="button"
            className={`dropdown-menu-item ${!value ? "active" : ""}`}
            onClick={() => {
              onChange("");
              setOpen(false);
            }}
          >
            <div className="item-row">
              <span className="status-dot ready" />
              <span className="item-title">{defaultLabel}</span>
              {!value && <Check size={13} className="check-icon" />}
            </div>
            <span className="item-sub">First ready attached endpoint</span>
          </button>
          {filtered.map((resource) => (
            <button
              key={resource.id}
              type="button"
              className={`dropdown-menu-item ${value === resource.id ? "active" : ""}`}
              onClick={() => {
                onChange(resource.id);
                setOpen(false);
              }}
            >
              <div className="item-row">
                <span
                  className={`status-dot ${resource.state === "ready" ? "ready" : "warning"}`}
                />
                <span className="item-title">{resource.name}</span>
                {value === resource.id && (
                  <Check size={13} className="check-icon" />
                )}
              </div>
              <span className="item-sub">
                {resource.url} · {stateLabel(resource)}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

type ComputeTab = "all_purpose" | "sql_warehouse" | "rag" | "all";

const computeTabs: { id: ComputeTab; label: string; desc: string }[] = [
  {
    id: "all",
    label: "All compute",
    desc: "Compute endpoints attached to this workspace. Substrate Data stores no engine and starts no compute: it routes work to endpoints that you operate.",
  },
  {
    id: "sql_warehouse",
    label: "SQL warehouses",
    desc: "Attach SQL execution endpoints for queries, data preview and application access.",
  },
  {
    id: "all_purpose",
    label: "All-purpose compute",
    desc: "Attach interactive and batch compute for profiling and ETL pipelines.",
  },
  {
    id: "rag",
    label: "AI Search",
    desc: "Attach text-chunking and embedding endpoints.",
  },
];

export function ManagedComputeScreen() {
  const client = useQueryClient();
  const [activeTab, setActiveTab] = useState<ComputeTab>("all");
  const [filterQuery, setFilterQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [error, setError] = useState("");
  const [pending, setPending] = useState("");
  const [selected, setSelected] = useState<ComputeResource | null>(null);
  const [showAttachModal, setShowAttachModal] = useState(false);
  const [editingResource, setEditingResource] =
    useState<ComputeResource | null>(null);
  const [detailsTab, setDetailsTab] = useState<
    "specs" | "access" | "events" | "workloads"
  >("specs");

  const resources = useQuery({
    queryKey: ["compute-resources"],
    queryFn: () =>
      api<{ resources: ComputeResource[] }>("/compute/resources").then(
        (r) => r.resources,
      ),
    refetchInterval: 2000,
  });

  const jobs = useQuery({
    queryKey: ["compute-jobs"],
    queryFn: () =>
      api<{ jobs: Job[] }>("/jobs?page_size=50")
        .then((r) => r.jobs)
        .catch(() => [] as Job[]),
    refetchInterval: 2000,
  });

  const events = useQuery({
    queryKey: ["compute-events", selected?.id],
    queryFn: () =>
      api<{ events: { id: number; at: number; message: string }[] }>(
        `/compute/resources/${selected?.id}/events`,
      ).then((r) => r.events),
    enabled: !!selected,
    refetchInterval: 3000,
  });

  async function act(key: string, path: string) {
    setError("");
    setPending(key);
    try {
      await api(path, post({}));
      await client.invalidateQueries({ queryKey: ["compute-resources"] });
      await client.invalidateQueries({ queryKey: ["compute-jobs"] });
    } catch (error) {
      setError(String(error));
    } finally {
      setPending("");
    }
  }

  async function detach(resource: ComputeResource) {
    setError("");
    setPending(resource.id);
    try {
      if (resource.desired === "running")
        await api(`/compute/resources/${resource.id}:stop`, post({}));
      await api(`/compute/resources/${resource.id}:delete`, post({}));
      await client.invalidateQueries({ queryKey: ["compute-resources"] });
      await client.invalidateQueries({ queryKey: ["compute-jobs"] });
    } catch (error) {
      setError(String(error));
    } finally {
      setPending("");
    }
  }

  const currentTabMeta =
    computeTabs.find((t) => t.id === activeTab) || computeTabs[0];

  const filteredResources = (resources.data ?? [])
    .filter((resource) => {
      if (activeTab === "all") return true;
      if (activeTab === "all_purpose")
        return kindOf(resource) === "all_purpose";
      if (activeTab === "sql_warehouse")
        return kindOf(resource) === "sql_warehouse";
      if (activeTab === "rag")
        return kindOf(resource) === "rag" || kindOf(resource) === "inference";
      return true;
    })
    .filter((r) => {
      if (!filterQuery.trim()) return true;
      return (
        r.name.toLowerCase().includes(filterQuery.toLowerCase()) ||
        (r.url ?? "").toLowerCase().includes(filterQuery.toLowerCase())
      );
    })
    .filter((r) => {
      if (statusFilter === "all") return true;
      if (statusFilter === "ready") return r.state === "ready";
      if (statusFilter === "attention") return r.state !== "ready";
      return true;
    });

  // Current active resource if updated in background
  const currentSelected = selected
    ? resources.data?.find((r) => r.id === selected.id) || selected
    : null;

  // ----------------------------------------------------
  // DETAILS VIEW (when a compute resource is clicked)
  // ----------------------------------------------------
  if (currentSelected) {
    const resourceJobs = (jobs.data ?? []).filter(
      (j) => j.resource_id === currentSelected.id,
    );

    return (
      <div className="compute-details-view">
        <div className="compute-details-nav">
          <button
            type="button"
            className="back-btn text-link"
            onClick={() => setSelected(null)}
          >
            <ArrowLeft size={14} />
            Back to compute
          </button>
        </div>

        <div className="compute-details-header">
          <div>
            <div className="compute-title-row">
              <h1>{currentSelected.name}</h1>
              <span
                className={`badge compute-status-badge ${
                  currentSelected.state === "ready"
                    ? "success"
                    : currentSelected.state === "starting"
                      ? ""
                      : "warning"
                }`}
              >
                {stateLabel(currentSelected)}
              </span>
            </div>
            <p className="muted">
              {kindLabels[kindOf(currentSelected)]} · Operated outside this
              application
              {currentSelected.last_error
                ? ` · ${currentSelected.last_error}`
                : ""}
            </p>
          </div>

          <div className="compute-details-actions">
            <Button
              variant="secondary"
              disabled={!!pending}
              onClick={() =>
                void act(
                  currentSelected.id,
                  `/compute/resources/${currentSelected.id}:${actionOf(currentSelected)}`,
                )
              }
            >
              {actionOf(currentSelected) === "stop" ? (
                <Square size={13} />
              ) : (
                <Play size={13} />
              )}
              {actionLabel(currentSelected)}
            </Button>
            <Button
              variant="secondary"
              disabled={currentSelected.desired === "running"}
              title={
                currentSelected.desired === "running"
                  ? "Pause routing before editing the attachment"
                  : undefined
              }
              onClick={() => {
                setEditingResource(currentSelected);
                setShowAttachModal(true);
              }}
            >
              Edit endpoint
            </Button>
            <Button
              variant="secondary"
              disabled={!!pending}
              aria-label={`Detach ${currentSelected.name}`}
              title="Detach from this workspace (the compute itself keeps running)"
              onClick={async () => {
                await detach(currentSelected);
                setSelected(null);
              }}
            >
              <Trash2 size={13} />
              Detach
            </Button>
          </div>
        </div>

        {error && (
          <p className="alert error" role="alert">
            {error}
          </p>
        )}

        <div className="tabs compute-details-tabs" role="tablist">
          <button
            role="tab"
            aria-selected={detailsTab === "specs"}
            className={detailsTab === "specs" ? "selected" : ""}
            onClick={() => setDetailsTab("specs")}
          >
            Configuration & Specs
          </button>
          {(currentSelected.role === "sql" ||
            kindOf(currentSelected) === "all_purpose") && (
            <button
              role="tab"
              aria-selected={detailsTab === "access"}
              className={detailsTab === "access" ? "selected" : ""}
              onClick={() => setDetailsTab("access")}
            >
              Application Access
            </button>
          )}
          <button
            role="tab"
            aria-selected={detailsTab === "events"}
            className={detailsTab === "events" ? "selected" : ""}
            onClick={() => setDetailsTab("events")}
          >
            Connection Events ({events.data?.length ?? 0})
          </button>
          <button
            role="tab"
            aria-selected={detailsTab === "workloads"}
            className={detailsTab === "workloads" ? "selected" : ""}
            onClick={() => setDetailsTab("workloads")}
          >
            Workload Runs ({resourceJobs.length})
          </button>
        </div>

        {detailsTab === "specs" && (
          <div className="panel compute-details-panel">
            <h2>Endpoint Specifications</h2>
            <dl className="details-grid">
              <div>
                <dt>Endpoint Connection</dt>
                <dd>
                  {currentSelected.endpoint ? (
                    <code>{currentSelected.endpoint}</code>
                  ) : (
                    <span className="muted">
                      Managed via durable job queue API
                    </span>
                  )}
                </dd>
              </div>
              <div>
                <dt>Attached address</dt>
                <dd>
                  <code>{currentSelected.url ?? "—"}</code>
                </dd>
              </div>
              <div>
                <dt>Last health check</dt>
                <dd>
                  {currentSelected.checked
                    ? new Date(
                        currentSelected.checked * 1000,
                      ).toLocaleTimeString()
                    : "Not yet checked"}
                </dd>
              </div>
              <div>
                <dt>Capacity</dt>
                <dd>
                  Sized and operated by its owner; this application cannot
                  start, resize or stop it.
                </dd>
              </div>
              <div>
                <dt>Queue limit</dt>
                <dd>{currentSelected.max_queue} queued jobs</dd>
              </div>
              <div>
                <dt>Routing concurrency</dt>
                <dd>
                  {currentSelected.concurrency} concurrent requests / jobs
                </dd>
              </div>
              <div>
                <dt>Workspace / Project Scope</dt>
                <dd>
                  {currentSelected.project} (Owner: {currentSelected.owner})
                </dd>
              </div>
            </dl>
          </div>
        )}

        {detailsTab === "access" && (
          <div className="panel compute-details-panel">
            <h2>Application & Client Access</h2>
            <p className="muted">
              Connect external client applications, BI dashboards, or notebooks
              directly to this SQL endpoint using Arrow or JSON.
            </p>
            <ComputeApplicationAccess
              key={currentSelected.id}
              resourceId={currentSelected.id}
            />
          </div>
        )}

        {detailsTab === "events" && (
          <div className="panel compute-details-panel">
            <h2>Connection Events</h2>
            <div className="events-list">
              {events.data?.map((event) => (
                <div key={event.id} className="event-item">
                  <Clock size={13} className="event-icon" />
                  <span className="event-message">{event.message}</span>
                  <time className="event-time muted">
                    {new Date(event.at * 1000).toLocaleTimeString()}
                  </time>
                </div>
              ))}
              {(!events.data || events.data.length === 0) && (
                <p className="muted">No recent lifecycle events recorded.</p>
              )}
            </div>
          </div>
        )}

        {detailsTab === "workloads" && (
          <div className="panel compute-details-panel">
            <h2>Executed Workloads</h2>
            <table className="compute-table">
              <thead>
                <tr>
                  <th>Job Operation</th>
                  <th>Status</th>
                  <th>Submitted</th>
                  <th>Duration</th>
                </tr>
              </thead>
              <tbody>
                {resourceJobs.map((j) => (
                  <tr key={j.id}>
                    <td>
                      <strong>{j.operation}</strong>
                      <small>
                        <code>{j.id.slice(0, 12)}</code>
                      </small>
                    </td>
                    <td>
                      <span className="badge">{j.state}</span>
                    </td>
                    <td>{new Date(j.created * 1000).toLocaleTimeString()}</td>
                    <td>{j.attempts} attempt(s)</td>
                  </tr>
                ))}
                {resourceJobs.length === 0 && (
                  <tr>
                    <td colSpan={4} className="muted compute-pad">
                      No workloads have run on this compute resource yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}

        {/* Modal for editing endpoint */}
        {showAttachModal && editingResource && (
          <AttachComputeModal
            initial={editingResource}
            onClose={() => {
              setShowAttachModal(false);
              setEditingResource(null);
            }}
            onSave={async (value) => {
              await api(
                `/compute/resources/${editingResource.id}`,
                post(attachable(value), "PUT"),
              );
              setShowAttachModal(false);
              setEditingResource(null);
              await client.invalidateQueries({
                queryKey: ["compute-resources"],
              });
            }}
          />
        )}
      </div>
    );
  }

  // ----------------------------------------------------
  // LIST VIEW: Clean, Simple, Spacious Table (like Databricks)
  // ----------------------------------------------------
  return (
    <div className="compute-layout">
      {/* Secondary in-page nav bar (Tabs) matching Databricks Compute header */}
      <nav
        className="compute-nav-tabs tabs"
        role="tablist"
        aria-label="Compute workload sub-navigation"
      >
        {computeTabs.map((t) => {
          const count = (resources.data ?? []).filter((r) => {
            if (t.id === "all") return true;
            if (t.id === "all_purpose") return kindOf(r) === "all_purpose";
            if (t.id === "sql_warehouse") return kindOf(r) === "sql_warehouse";
            if (t.id === "rag")
              return kindOf(r) === "rag" || kindOf(r) === "inference";
            return true;
          }).length;
          return (
            <button
              key={t.id}
              role="tab"
              aria-selected={activeTab === t.id}
              className={activeTab === t.id ? "selected" : ""}
              onClick={() => {
                setActiveTab(t.id);
                setSelected(null);
              }}
            >
              {t.label}
              <span className="tab-count-badge">{count}</span>
            </button>
          );
        })}
      </nav>

      {/* Spacious, minimal toolbar matching Databricks */}
      <div className="compute-filter-bar">
        <div className="compute-search-input">
          <Search size={14} />
          <input
            type="text"
            placeholder="Filter compute you have access to…"
            value={filterQuery}
            onChange={(e) => setFilterQuery(e.target.value)}
          />
        </div>

        <select
          className="compute-filter-select"
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="all">All states</option>
          <option value="ready">Attached · ready</option>
          <option value="attention">Paused / unreachable</option>
        </select>

        <div className="compute-actions-right">
          <Button
            onClick={() => {
              setEditingResource(null);
              setShowAttachModal(true);
            }}
          >
            <Plus size={14} />
            Attach compute
          </Button>
        </div>
      </div>

      {(error || resources.error) && (
        <p className="alert error" role="alert">
          {error || resources.error?.message}
        </p>
      )}

      {/* Clean, spacious compute inventory table matching Databricks screenshot */}
      <section className="panel compute-table-wrap">
        <table className="compute-table databricks-style">
          <thead>
            <tr>
              <th style={{ width: "40px" }}>State</th>
              <th>Name</th>
              <th>Workload</th>
              <th>Attached address</th>
              <th>Routing concurrency</th>
              <th>Last checked</th>
              <th style={{ textAlign: "right" }}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {filteredResources.map((resource) => {
              const isReady = resource.state === "ready";
              return (
                <tr key={resource.id}>
                  <td>
                    {isReady ? (
                      <span title={stateLabel(resource)}>
                        <CheckCircle2
                          size={15}
                          className="status-icon running"
                        />
                      </span>
                    ) : (
                      <span title={stateLabel(resource)}>
                        <PauseCircle
                          size={15}
                          className="status-icon suspended"
                        />
                      </span>
                    )}
                  </td>
                  <td>
                    <button
                      type="button"
                      className="text-link compute-name-btn"
                      onClick={() => setSelected(resource)}
                      title="Click to view compute details"
                    >
                      {resource.name}
                    </button>
                  </td>
                  <td>
                    <span className="workload-tag">
                      {kindLabels[kindOf(resource)]}
                    </span>
                  </td>
                  <td>
                    <code className="runtime-label">{resource.url ?? "—"}</code>
                  </td>
                  <td>{resource.concurrency}</td>
                  <td>
                    <span className="muted">
                      {resource.checked
                        ? new Date(resource.checked * 1000).toLocaleTimeString()
                        : "—"}
                    </span>
                  </td>
                  <td style={{ textAlign: "right" }}>
                    <div
                      className="result-actions"
                      style={{ justifyContent: "flex-end" }}
                    >
                      <Button
                        variant="secondary"
                        disabled={!!pending}
                        title={
                          actionOf(resource) === "stop"
                            ? "Pause routing; the compute keeps running"
                            : "Resume routing to this compute"
                        }
                        onClick={() =>
                          void act(
                            resource.id,
                            `/compute/resources/${resource.id}:${actionOf(resource)}`,
                          )
                        }
                      >
                        {actionOf(resource) === "stop" ? (
                          <Square size={12} />
                        ) : (
                          <Play size={12} />
                        )}
                        {actionLabel(resource)}
                      </Button>
                      <Button
                        variant="secondary"
                        disabled={!!pending}
                        aria-label={`Detach ${resource.name}`}
                        title="Detach from this workspace (the compute itself keeps running)"
                        onClick={() => void detach(resource)}
                      >
                        <Trash2 size={13} />
                      </Button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {filteredResources.length === 0 && !resources.isPending && (
          <div className="compute-empty-state">
            <p className="muted">
              No compute endpoints found matching filters.
            </p>
            <Button
              variant="secondary"
              onClick={() => {
                setEditingResource(null);
                setShowAttachModal(true);
              }}
            >
              <Plus size={13} /> Attach an endpoint
            </Button>
          </div>
        )}

        {resources.isPending && (
          <p className="muted compute-pad">Loading compute endpoints…</p>
        )}
      </section>

      {/* Modal Dialog for Attaching / Editing compute */}
      {showAttachModal && (
        <AttachComputeModal
          initial={editingResource || undefined}
          defaultKind={activeTab === "all" ? "sql_warehouse" : activeTab}
          onClose={() => {
            setShowAttachModal(false);
            setEditingResource(null);
          }}
          onSave={async (value) => {
            if (editingResource) {
              await api(
                `/compute/resources/${editingResource.id}`,
                post(attachable(value), "PUT"),
              );
            } else {
              await api("/compute/resources", post(attachable(value)));
            }
            setShowAttachModal(false);
            setEditingResource(null);
            await client.invalidateQueries({ queryKey: ["compute-resources"] });
          }}
        />
      )}
    </div>
  );
}

// ----------------------------------------------------
// MODAL DIALOG: Attach / Edit Compute Endpoint
// ----------------------------------------------------
function AttachComputeModal({
  initial,
  defaultKind,
  onClose,
  onSave,
}: {
  initial?: ComputeResource;
  defaultKind?: string;
  onClose: () => void;
  onSave: (value: Record<string, unknown>) => Promise<void>;
}) {
  const [kind, setKind] = useState(
    initial ? kindOf(initial) : defaultKind || "sql_warehouse",
  );
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-container panel"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
      >
        <div className="modal-header">
          <div>
            <h2 id="modal-title">
              {initial ? "Edit attached endpoint" : "Attach compute endpoint"}
            </h2>
            <p
              className="muted"
              style={{ margin: "4px 0 0", fontSize: "11px" }}
            >
              Substrate Data decouples persistent storage & catalog metadata
              from compute execution.
            </p>
          </div>
          <button
            type="button"
            className="modal-close-btn"
            onClick={onClose}
            aria-label="Close modal"
          >
            <X size={16} />
          </button>
        </div>

        <form
          className="modal-form"
          onSubmit={async (event) => {
            event.preventDefault();
            setBusy(true);
            setError("");
            const data = new FormData(event.currentTarget);
            const value: Record<string, unknown> = {};
            for (const [key, item] of data) {
              if (key === "token" && item === "") continue;
              value[key] = ["concurrency", "max_queue"].includes(key)
                ? Number(item)
                : item;
            }
            try {
              await onSave(value);
            } catch (err) {
              setError(String(err));
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="form-grid compute-form">
            <label>
              Endpoint name
              <input
                name="name"
                required
                maxLength={80}
                placeholder="e.g. Databricks SQL Warehouse or Spark ETL"
                defaultValue={initial?.name}
              />
            </label>

            <label>
              Workload type
              <select
                name="kind"
                value={kind}
                onChange={(e) => setKind(e.target.value)}
              >
                <option value="sql_warehouse">
                  SQL warehouse (Queries, BI & Preview)
                </option>
                <option value="all_purpose">
                  All-purpose compute (Profiling & ETL pipelines)
                </option>
                <option value="rag">
                  AI Search / RAG (Text chunking & search)
                </option>
                <option value="inference">
                  Embedding inference (Vector models)
                </option>
              </select>
            </label>

            <input
              type="hidden"
              name="role"
              value={
                {
                  sql_warehouse: "sql",
                  all_purpose: "jobs",
                  rag: "rag",
                  inference: "inference",
                }[kind]
              }
            />

            <label style={{ gridColumn: "1 / -1" }}>
              Compute address
              <input
                name="url"
                type="url"
                required
                placeholder="https://compute.example.com"
                defaultValue={initial?.url ?? ""}
              />
            </label>

            <label style={{ gridColumn: "1 / -1" }}>
              Connection token
              <input
                name="token"
                type="password"
                autoComplete="off"
                required={!initial}
                minLength={32}
                placeholder={
                  initial
                    ? "Leave blank to keep the stored token"
                    : "Shared secret configured on the compute"
                }
              />
            </label>
            <p className="muted" style={{ gridColumn: "1 / -1", margin: 0 }}>
              Substrate Data attaches to compute that you run elsewhere. It
              never starts, sizes or stops it, and the token is stored by the
              controller and never shown again.
            </p>
          </div>

          <details className="advanced-options" style={{ marginTop: "12px" }}>
            <summary>Routing limits & scope</summary>
            <div
              className="form-grid compute-form"
              style={{ marginTop: "8px" }}
            >
              <label>
                Queue limit
                <input
                  name="max_queue"
                  type="number"
                  min={1}
                  max={10000}
                  defaultValue={initial?.max_queue ?? 100}
                />
              </label>
              <label>
                Concurrent requests routed
                <input
                  name="concurrency"
                  type="number"
                  min={1}
                  max={16}
                  defaultValue={initial?.concurrency ?? 2}
                />
              </label>
              <label>
                Owner
                <input
                  name="owner"
                  required
                  defaultValue={initial?.owner ?? "local"}
                />
              </label>
              <label>
                Project scope
                <input
                  name="project"
                  required
                  defaultValue={initial?.project ?? "default"}
                />
              </label>
            </div>
          </details>

          {error && (
            <p role="alert" className="alert error">
              {error}
            </p>
          )}

          <div className="modal-footer">
            <Button variant="secondary" type="button" onClick={onClose}>
              Cancel
            </Button>
            <Button disabled={busy}>
              {busy
                ? "Saving…"
                : initial
                  ? "Save attached endpoint"
                  : "Attach compute endpoint"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
