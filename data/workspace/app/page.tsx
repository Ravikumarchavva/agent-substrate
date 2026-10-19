"use client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  Check,
  ChevronRight,
  Database,
  LayoutDashboard,
  LoaderCircle,
  Search,
  Server,
  Terminal,
  Workflow,
  X,
} from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { ComputeScreen } from "../components/screens/compute";
import { RetrievalScreen } from "../components/screens/ai-retrieval";
import { ExplorerScreen } from "../components/screens/data-explorer";
import { OverviewScreen } from "../components/screens/overview";
import { PipelinesScreen } from "../components/screens/pipelines";
import { SqlScreen } from "../components/screens/sql-workspace";
import { SidebarToggleIcon } from "../components/ui/sidebar-toggle-icon";
import { LoamzMark } from "../components/ui/loamz-mark";
import { UserMenu } from "../components/user-menu";
import { usePanelPreference } from "../hooks/use-panel-preference";
import { useWorkspace } from "../hooks/use-workspace";

const navSections = [
  {
    heading: "",
    items: [
      { section: "Overview", label: "Workspace", icon: LayoutDashboard },
      { section: "Data explorer", label: "Catalog", icon: Database },
      { section: "Pipelines", label: "Jobs & Pipelines", icon: Workflow },
      { section: "Compute", label: "Compute", icon: Server },
    ],
  },
  {
    heading: "SQL",
    items: [{ section: "SQL workspace", label: "SQL Editor", icon: Terminal }],
  },
  {
    heading: "AI / ML",
    items: [
      { section: "AI & retrieval", label: "AI & Retrieval", icon: Server },
    ],
  },
];

const allNavItems = navSections.flatMap((s) => s.items);

export default function Page() {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <Workspace />
    </QueryClientProvider>
  );
}
function Workspace() {
  const model = useWorkspace();
  const [sidebarCollapsed, setSidebarCollapsed] = usePanelPreference(
    "main-collapsed",
    false,
  );
  const {
    client,
    section,
    setSection,
    setSelected,
    search,
    setSearch,
    setError,
    notice,
    datasets,
    current,
    activeError,
  } = model;
  const active =
    allNavItems.find((item) => item.section === section) || allNavItems[0];
  const title =
    section === "Data explorer" && current
      ? current.display_name || current.name
      : active.label;
  return (
    <div
      className={`workspace ${sidebarCollapsed ? "sidebar-collapsed" : ""} ${section === "SQL workspace" ? "workspace-ide" : ""}`}
    >
      <aside
        className={`sidebar ${sidebarCollapsed ? "collapsed" : ""}`}
        aria-label="Main Navigation"
      >
        <div className="sidebar-header">
          <Link
            className="brand"
            href="/"
            aria-label="Loamz Data"
            title="Loamz Data"
          >
            <LoamzMark className="app-brand-icon" width={22} height={22} />
            {!sidebarCollapsed && (
              <span>
                Loamz <strong>Data</strong>
              </span>
            )}
          </Link>
          <button
            className="sidebar-collapse-toggle"
            aria-label={
              sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"
            }
            title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
          >
            <SidebarToggleIcon
              direction={sidebarCollapsed ? "open" : "close"}
              width={18}
              height={18}
            />
          </button>
        </div>
        <nav aria-label="Workspace">
          {navSections.map((group, groupIdx) => (
            <div key={groupIdx} className="nav-group">
              {!sidebarCollapsed && group.heading && (
                <div className="nav-group-heading">{group.heading}</div>
              )}
              {group.items.map(({ section: target, label, icon: Icon }) => (
                <button
                  key={target}
                  aria-label={label}
                  title={sidebarCollapsed ? label : undefined}
                  aria-current={section === target ? "page" : undefined}
                  className={
                    section === target ? "nav-item active" : "nav-item"
                  }
                  onClick={() => {
                    setSection(target);
                    setError("");
                  }}
                >
                  <Icon size={16} />
                  {!sidebarCollapsed && <span>{label}</span>}
                  {!sidebarCollapsed && target === "Data explorer" && (
                    <small className="nav-count">
                      {datasets.data?.length ?? 0}
                    </small>
                  )}
                </button>
              ))}
            </div>
          ))}
        </nav>
      </aside>
      <div
        className={`main-shell ${sidebarCollapsed ? "shell-expanded" : ""} ${section === "SQL workspace" ? "shell-ide" : ""}`}
      >
        <header className="topbar">
          <div className="breadcrumbs">
            {section === "Data explorer" && current ? (
              <>
                <button
                  type="button"
                  className="text-link"
                  onClick={() => setSelected(null)}
                >
                  Catalog
                </button>
                <ChevronRight size={12} />
                <strong>{current.display_name || current.name}</strong>
              </>
            ) : (
              <strong>{active.label}</strong>
            )}
          </div>
          <label className="global-search">
            <Search size={14} />
            <input
              aria-label="Search catalog"
              placeholder="Search catalog…"
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                setSelected(null);
                setSection("Data explorer");
              }}
            />
          </label>
          <UserMenu />
        </header>
        <main className={section === "SQL workspace" ? "main-full-bleed" : ""}>
          {section !== "SQL workspace" && (
            <div className="page-heading">
              <div>
                <h1>{title}</h1>
                {section === "Data explorer" && !current && (
                  <p>{datasets.data?.length ?? 0} tables</p>
                )}
              </div>
            </div>
          )}
          {model.catalog.data?.assetsAvailable === false && (
            <p className="catalog-api-notice" role="status">
              Restart the data API to enable Volumes and Models.
            </p>
          )}
          {activeError && (
            <div className="alert error" role="alert">
              <X size={15} />
              <span>{activeError}</span>
              <button
                onClick={() => {
                  setError("");
                  void client.invalidateQueries();
                }}
              >
                Retry
              </button>
            </div>
          )}
          {notice && (
            <div className="alert success" role="status">
              <Check size={15} />
              <span>{notice}</span>
              <button
                aria-label="Dismiss notification"
                onClick={() => model.setNotice("")}
              >
                <X size={13} />
              </button>
            </div>
          )}
          {datasets.isPending && (
            <div className="loading">
              <LoaderCircle size={16} className="spin" />
              Loading tables…
            </div>
          )}
          {section === "Compute" && <ComputeScreen />}
          {section === "Overview" && <OverviewScreen model={model} />}
          {section === "Data explorer" && <ExplorerScreen model={model} />}
          {section === "SQL workspace" && <SqlScreen model={model} />}
          {section === "Pipelines" && <PipelinesScreen model={model} />}
          {section === "AI & retrieval" && <RetrievalScreen model={model} />}
        </main>
      </div>
    </div>
  );
}
