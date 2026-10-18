"use client";

import {
  ArrowDownToLine,
  BookOpen,
  Box,
  Check,
  ChevronDown,
  ChevronRight,
  ChevronsRight,
  Database,
  Download,
  Eye,
  Folder,
  History,
  Layers,
  LoaderCircle,
  Maximize2,
  Minimize2,
  Play,
  Plus,
  RefreshCw,
  Search,
  SlidersHorizontal,
  Square,
  Table as TableIcon,
  Terminal,
  X,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { WorkspaceModel } from "../../hooks/use-workspace";
import { api, post } from "../api";
import { Empty } from "../empty";
import { Grid } from "../grid";
import type { OnMount } from "@monaco-editor/react";
import { configureEditorThemes, Editor } from "../sql-editor";
import { useTheme } from "../theme";
import { SidebarToggleIcon } from "../ui/sidebar-toggle-icon";
import { usePanelPreference } from "../../hooks/use-panel-preference";
import { ResourcePicker } from "./managed-compute";

type QueryTab = {
  id: string;
  title: string;
  sql: string;
};

export function SqlScreen({ model }: { model: WorkspaceModel }) {
  const { resolved } = useTheme();
  const [computeId, setComputeId] = useState("");
  const [sideOpen, setSideOpen] = usePanelPreference("sql-catalog-open", true);
  const [searchTree, setSearchTree] = useState("");
  const [expandedNodes, setExpandedNodes] = useState<Record<string, boolean>>({
    org: true,
    main: true,
    main_default: true,
    main_nyc: true,
  });
  const [expandedTables, setExpandedTables] = useState<Record<string, boolean>>(
    {},
  );

  // Horizontal Splitter for Catalog Metadata Pane (default 280px, draggable 180px - 600px)
  const [sidebarWidth, setSidebarWidth] = useState(280);
  const [isDraggingSidebarResizer, setIsDraggingSidebarResizer] =
    useState(false);

  const startSidebarResize = (e: React.MouseEvent) => {
    e.preventDefault();
    setIsDraggingSidebarResizer(true);
    const startX = e.clientX;
    const startW = sidebarWidth;

    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    const onMouseMove = (moveEvent: MouseEvent) => {
      const delta = moveEvent.clientX - startX;
      const newWidth = Math.max(180, Math.min(600, startW + delta));
      setSidebarWidth(newWidth);
    };

    const onMouseUp = () => {
      setIsDraggingSidebarResizer(false);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
  };

  // Resizable Query Editor Splitter (default 250px, draggable between 100px and 700px)
  const [editorHeight, setEditorHeight] = useState(240);
  const [isDraggingResizer, setIsDraggingResizer] = useState(false);

  // Maximize view mode: "none" (split) | "results" (results full height, editor hidden) | "editor" (editor full height, results hidden)
  const [maximizedView, setMaximizedView] = useState<
    "none" | "editor" | "results"
  >("none");

  const startResize = (e: React.MouseEvent) => {
    e.preventDefault();
    setIsDraggingResizer(true);
    const startY = e.clientY;
    const startH = editorHeight;

    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";

    const onMouseMove = (moveEvent: MouseEvent) => {
      const delta = moveEvent.clientY - startY;
      const newHeight = Math.max(
        100,
        Math.min(window.innerHeight - 180, startH + delta),
      );
      setEditorHeight(newHeight);
    };

    const onMouseUp = () => {
      setIsDraggingResizer(false);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
  };

  // Query tabs state
  const [tabs, setTabs] = useState<QueryTab[]>([
    {
      id: "tab-1",
      title: "New Query 1",
      sql: model.sql || "SELECT 1 AS ready;",
    },
  ]);
  const [activeTabId, setActiveTabId] = useState("tab-1");
  const [lastSaved, setLastSaved] = useState<string>("just now");

  const tree = model.catalog.data?.catalogs ?? [];

  const CATALOG_SCHEMAS: Record<string, string[]> = Object.fromEntries(
    tree.map((c) => [c.name, c.schemas.map((s) => s.name)]),
  );

  const [selectedCatalog, setSelectedCatalog] = useState("main");
  const [catalogOpen, setCatalogOpen] = useState(false);
  const catalogRef = useRef<HTMLDivElement>(null);

  const [selectedSchema, setSelectedSchema] = useState("default");
  const [schemaOpen, setSchemaOpen] = useState(false);
  const schemaRef = useRef<HTMLDivElement>(null);

  const [downloadOpen, setDownloadOpen] = useState(false);
  const downloadRef = useRef<HTMLDivElement>(null);

  const formatRuntime = (durationMs: number) => {
    if (durationMs < 1000) {
      return `${durationMs}ms`;
    }
    return `${(durationMs / 1000).toFixed(3)}s`;
  };

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        catalogRef.current &&
        !catalogRef.current.contains(event.target as Node)
      ) {
        setCatalogOpen(false);
      }
      if (
        schemaRef.current &&
        !schemaRef.current.contains(event.target as Node)
      ) {
        setSchemaOpen(false);
      }
      if (
        downloadRef.current &&
        !downloadRef.current.contains(event.target as Node)
      ) {
        setDownloadOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const {
    sql,
    setSql,
    result,
    busy,
    queryId,
    setError,
    datasets,
    history,
    execute: runStatement,
    download: downloadStatement,
    setResult,
  } = model;

  const execute = useCallback(
    (resource = computeId, statement = sql) =>
      runStatement(resource, statement, {
        catalog: selectedCatalog,
        schema: selectedSchema,
      }),
    [computeId, sql, selectedCatalog, selectedSchema, runStatement],
  );
  const download = (format: "csv" | "arrow", resource = computeId) =>
    downloadStatement(format, resource, {
      catalog: selectedCatalog,
      schema: selectedSchema,
    });

  const activeTab = tabs.find((t) => t.id === activeTabId) || tabs[0];
  const editorRef = useRef<Parameters<OnMount>[0] | null>(null);

  const handleSqlChange = (newSql: string) => {
    setSql(newSql);
    setTabs((prev) =>
      prev.map((t) => (t.id === activeTabId ? { ...t, sql: newSql } : t)),
    );
    setLastSaved("just now");
  };

  const insertIntoEditor = (text: string) => {
    if (editorRef.current) {
      const editor = editorRef.current;
      const selection = editor.getSelection();
      if (selection) {
        editor.executeEdits("insert-text", [
          {
            range: selection,
            text: text,
            forceMoveMarkers: true,
          },
        ]);
        editor.focus();
        return;
      }
    }
    const current = sql.trim();
    if (!current || current.includes("SELECT 1 AS ready")) {
      handleSqlChange(`SELECT * FROM ${text} LIMIT 100;`);
    } else {
      handleSqlChange(`${sql} ${text}`);
    }
  };

  const handleSwitchTab = (tabId: string) => {
    if (busy) return;
    const target = tabs.find((t) => t.id === tabId);
    if (target) {
      setResult(null);
      setActiveTabId(tabId);
      setSql(target.sql);
    }
  };

  const handleAddTab = () => {
    if (busy) return;
    setResult(null);
    const newId = `tab-${Date.now()}`;
    const newTab: QueryTab = {
      id: newId,
      title: `Query ${tabs.length + 1}`,
      sql: "SELECT 1;",
    };
    setTabs([...tabs, newTab]);
    setActiveTabId(newId);
    setSql(newTab.sql);
  };

  const handleCloseTab = (e: React.MouseEvent, tabId: string) => {
    e.stopPropagation();
    if (busy || tabs.length === 1) return;
    const nextTabs = tabs.filter((t) => t.id !== tabId);
    setTabs(nextTabs);
    if (activeTabId === tabId) {
      setResult(null);
      setActiveTabId(nextTabs[0].id);
      setSql(nextTabs[0].sql);
    }
  };

  const saveSqlFile = () => {
    const url = URL.createObjectURL(
      new Blob([sql], { type: "text/plain;charset=utf-8" }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `${activeTab.title.replace(/[^a-zA-Z0-9_-]/g, "_")}.sql`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const toggleNode = (nodeId: string) => {
    setExpandedNodes((prev) => ({ ...prev, [nodeId]: !prev[nodeId] }));
  };

  const toggleTable = (tableId: string) => {
    setExpandedTables((prev) => ({ ...prev, [tableId]: !prev[tableId] }));
  };

  // Keyboard shortcut Ctrl+Enter to execute
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        if (!busy && sql.trim()) {
          void execute(computeId);
        }
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [busy, sql, computeId, execute]);

  const getTablesForCatalogSchema = (catalog: string, schema: string) => {
    const found = tree
      .find((c) => c.name === catalog)
      ?.schemas.find((x) => x.name === schema);
    const matches = (name: string) =>
      name.toLowerCase().includes(searchTree.toLowerCase());
    return (found?.tables ?? [])
      .filter((d) => matches(d.display_name))
      .map((d) => ({ id: d.id, name: d.display_name, columns: d.columns }));
  };

  const getViewsForCatalogSchema = (catalog: string, schema: string) => {
    const found = tree
      .find((c) => c.name === catalog)
      ?.schemas.find((x) => x.name === schema);
    const matches = (name: string) =>
      name.toLowerCase().includes(searchTree.toLowerCase());
    const rawViews = found?.views ?? [];

    return rawViews
      .filter((v) => matches(v.name))
      .map((v) => ({
        id: v.id,
        name: v.name,
        sql: v.sql,
        columns: v.columns.map((c) => ({
          name: c.name,
          dtype:
            (c as { dtype?: string; type?: string }).dtype ||
            (c as { dtype?: string; type?: string }).type ||
            "string",
        })),
      }));
  };

  const renderTableItem = (
    catalog: string,
    schema: string,
    tableName: string,
    tableId: string,
    columns: Array<{ name: string; dtype: string }>,
  ) => {
    const isTableExpanded = expandedTables[tableId];
    const fullPath = `${catalog}.${schema}.${tableName}`;
    return (
      <div key={tableId} className="tree-table-item">
        <div className="tree-table-row">
          <button
            type="button"
            className="tree-expand-btn"
            title={isTableExpanded ? "Collapse columns" : "Expand columns"}
            onClick={() => toggleTable(tableId)}
          >
            <ChevronRight
              size={11}
              className={`tree-arrow ${isTableExpanded ? "open" : ""}`}
            />
          </button>
          <button
            type="button"
            className="tree-table-label"
            title={`SELECT * FROM ${fullPath} LIMIT 100;`}
            onClick={() =>
              handleSqlChange(`SELECT * FROM ${fullPath} LIMIT 100;`)
            }
          >
            <TableIcon size={12} className="icon-table" />
            <span className="table-name-text">{tableName}</span>
          </button>

          <div className="tree-table-actions">
            <button
              type="button"
              className="tree-action-btn"
              title="Quick preview"
              onClick={(e) => {
                e.stopPropagation();
                handleSqlChange(`SELECT * FROM ${fullPath} LIMIT 20;`);
                void execute(computeId, `SELECT * FROM ${fullPath} LIMIT 20;`);
              }}
            >
              <Search size={11} />
            </button>
            <div className="tooltip-wrapper">
              <button
                type="button"
                className="tree-action-btn insert-name-btn"
                aria-label="Insert table name"
                onClick={(e) => {
                  e.stopPropagation();
                  insertIntoEditor(fullPath);
                }}
              >
                <ChevronsRight size={12} />
              </button>
              <span className="databricks-tooltip">Insert table name</span>
            </div>
          </div>
        </div>

        {isTableExpanded && (
          <div className="tree-columns-list">
            {columns.map((c) => (
              <button
                key={c.name}
                type="button"
                className="tree-column-item"
                title={`Insert column ${c.name}`}
                onClick={() => {
                  insertIntoEditor(c.name);
                }}
              >
                <span className="column-type-icon">
                  {c.dtype.toLowerCase().includes("int") ||
                  c.dtype.toLowerCase().includes("float")
                    ? "#"
                    : c.dtype.toLowerCase().includes("date") ||
                        c.dtype.toLowerCase().includes("time")
                      ? "📅"
                      : "Abc"}
                </span>
                <span className="column-name">{c.name}</span>
                <span className="column-dtype">{c.dtype}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    );
  };

  const renderViewItem = (
    catalog: string,
    schema: string,
    viewName: string,
    viewId: string,
    columns: Array<{ name: string; dtype: string }>,
  ) => {
    const isViewExpanded = expandedTables[viewId];
    const fullPath = `${catalog}.${schema}.${viewName}`;
    return (
      <div key={viewId} className="tree-table-item">
        <div className="tree-table-row">
          <button
            type="button"
            className="tree-expand-btn"
            title={isViewExpanded ? "Collapse columns" : "Expand columns"}
            onClick={() => toggleTable(viewId)}
          >
            <ChevronRight
              size={11}
              className={`tree-arrow ${isViewExpanded ? "open" : ""}`}
            />
          </button>
          <button
            type="button"
            className="tree-table-label"
            title={`SELECT * FROM ${fullPath} LIMIT 100;`}
            onClick={() =>
              handleSqlChange(`SELECT * FROM ${fullPath} LIMIT 100;`)
            }
          >
            <Eye size={12} className="icon-view" />
            <span className="table-name-text">{viewName}</span>
          </button>

          <div className="tree-table-actions">
            <button
              type="button"
              className="tree-action-btn"
              title="Quick preview"
              onClick={(e) => {
                e.stopPropagation();
                handleSqlChange(`SELECT * FROM ${fullPath} LIMIT 20;`);
                void execute(computeId, `SELECT * FROM ${fullPath} LIMIT 20;`);
              }}
            >
              <Search size={11} />
            </button>
            <div className="tooltip-wrapper">
              <button
                type="button"
                className="tree-action-btn insert-name-btn"
                aria-label="Insert view name"
                onClick={(e) => {
                  e.stopPropagation();
                  insertIntoEditor(fullPath);
                }}
              >
                <ChevronsRight size={12} />
              </button>
              <span className="databricks-tooltip">Insert view name</span>
            </div>
          </div>
        </div>

        {isViewExpanded && (
          <div className="tree-columns-list">
            {columns.length === 0 ? (
              <div className="tree-empty-notice">No columns listed</div>
            ) : (
              columns.map((c) => (
                <button
                  key={c.name}
                  type="button"
                  className="tree-column-item"
                  title={`Insert column ${c.name}`}
                  onClick={() => {
                    insertIntoEditor(c.name);
                  }}
                >
                  <span className="column-type-icon">
                    {c.dtype.toLowerCase().includes("int") ||
                    c.dtype.toLowerCase().includes("float")
                      ? "#"
                      : c.dtype.toLowerCase().includes("date") ||
                          c.dtype.toLowerCase().includes("time")
                        ? "📅"
                        : "Abc"}
                  </span>
                  <span className="column-name">{c.name}</span>
                  <span className="column-dtype">{c.dtype}</span>
                </button>
              ))
            )}
          </div>
        )}
      </div>
    );
  };

  const renderSchemaNode = (catalog: string, schema: string) => {
    const nodeKey = `${catalog}_${schema}`;
    const tablesGroupKey = `${nodeKey}_tables`;
    const viewsGroupKey = `${nodeKey}_views`;

    const isSchemaExpanded =
      expandedNodes[nodeKey] ??
      (catalog === "main" && (schema === "default" || schema === "nyc"));
    const tables = getTablesForCatalogSchema(catalog, schema);
    const views = getViewsForCatalogSchema(catalog, schema);
    const isTablesExpanded =
      expandedNodes[tablesGroupKey] ??
      (tables.length > 0 || (schema === "default" && views.length === 0));
    const isViewsExpanded = expandedNodes[viewsGroupKey] ?? views.length > 0;

    return (
      <div className="tree-group" key={nodeKey}>
        <button
          type="button"
          className="tree-node schema-node"
          onClick={() => toggleNode(nodeKey)}
        >
          <ChevronRight
            size={12}
            className={`tree-arrow ${isSchemaExpanded ? "open" : ""}`}
          />
          <Database size={13} className="icon-schema" />
          <span>{schema}</span>
        </button>

        {isSchemaExpanded && (
          <div className="tree-children">
            {/* Tables (N) */}
            <div className="tree-group">
              <button
                type="button"
                className="tree-node tables-header-node"
                onClick={() => toggleNode(tablesGroupKey)}
              >
                <ChevronRight
                  size={11}
                  className={`tree-arrow ${isTablesExpanded ? "open" : ""}`}
                />
                <TableIcon size={12} className="icon-table" />
                <span>Tables ({tables.length})</span>
              </button>

              {isTablesExpanded && (
                <div className="tree-children">
                  {tables.length === 0 ? (
                    <div className="tree-empty-notice">
                      No tables in this schema
                    </div>
                  ) : (
                    tables.map((t) =>
                      renderTableItem(catalog, schema, t.name, t.id, t.columns),
                    )
                  )}
                </div>
              )}
            </div>

            {/* Views (M) */}
            <div className="tree-group">
              <button
                type="button"
                className="tree-node views-header-node"
                onClick={() => toggleNode(viewsGroupKey)}
              >
                <ChevronRight
                  size={11}
                  className={`tree-arrow ${isViewsExpanded ? "open" : ""}`}
                />
                <Eye size={12} className="icon-view" />
                <span>Views ({views.length})</span>
              </button>

              {isViewsExpanded && (
                <div className="tree-children">
                  {views.length === 0 ? (
                    <div className="tree-empty-notice">
                      No views in this schema
                    </div>
                  ) : (
                    views.map((v) =>
                      renderViewItem(catalog, schema, v.name, v.id, v.columns),
                    )
                  )}
                </div>
              )}
            </div>
            {(["volumes", "models"] as const).map((kind) => {
              const items =
                tree
                  .find((c) => c.name === catalog)
                  ?.schemas.find((item) => item.name === schema)?.[kind] ?? [];
              return (
                <details className="catalog-object-group" key={kind}>
                  <summary>
                    {kind === "volumes" ? (
                      <Folder size={13} />
                    ) : (
                      <Box size={13} />
                    )}{" "}
                    {kind === "volumes" ? "Volumes" : "Models"} ({items.length})
                  </summary>
                  {items
                    .filter((item) =>
                      item.name
                        .toLowerCase()
                        .includes(searchTree.toLowerCase()),
                    )
                    .map((item) => (
                      <div
                        className="catalog-asset-tree-item"
                        key={item.id}
                        title={item.full_name}
                      >
                        {item.name}
                        <small>
                          {item.kind === "model"
                            ? item.properties.framework
                            : "Volume"}
                        </small>
                      </div>
                    ))}
                </details>
              );
            })}
          </div>
        )}
      </div>
    );
  };

  const renderCatalogNode = (catalogName: string, isDefault = false) => {
    const isExpanded = expandedNodes[catalogName];
    const schemas = CATALOG_SCHEMAS[catalogName] || [];

    return (
      <div className="tree-group" key={catalogName}>
        <button
          type="button"
          className={`tree-node ${isDefault ? "active-project-node" : ""}`}
          onClick={() => toggleNode(catalogName)}
        >
          <ChevronRight
            size={12}
            className={`tree-arrow ${isExpanded ? "open" : ""}`}
          />
          <BookOpen size={13} className="icon-catalog" />
          {isDefault ? (
            <strong>{catalogName}</strong>
          ) : (
            <span>{catalogName}</span>
          )}
        </button>

        {isExpanded && (
          <div className="tree-children">
            {schemas.map((sch) => renderSchemaNode(catalogName, sch))}
          </div>
        )}
      </div>
    );
  };

  return (
    <div
      className={`sql-layout databricks-sql-layout ${sideOpen ? "" : "sql-collapsed"}`}
      style={{
        display: "flex",
        flexDirection: "row",
        width: "100%",
        height: "100%",
      }}
    >
      {/* LEFT SIDEBAR: Databricks Catalog Tree */}
      {sideOpen ? (
        <>
          <aside
            className="sql-sidebar databricks-catalog-pane"
            style={{ width: `${sidebarWidth}px`, flexShrink: 0 }}
          >
            <div className="catalog-tree-header">
              <div className="catalog-tree-title">
                <Database size={13} className="icon-catalog" />
                <span>Catalog</span>
              </div>
              <div className="catalog-tree-actions">
                <button
                  type="button"
                  className="panel-toggle-btn"
                  title="Refresh catalog"
                  onClick={() => void datasets.refetch()}
                >
                  <RefreshCw size={12} />
                </button>
                <button
                  type="button"
                  className="panel-toggle-btn"
                  aria-label="Collapse SQL sidebar"
                  title="Collapse sidebar"
                  onClick={() => setSideOpen(false)}
                >
                  <SidebarToggleIcon direction="close" width={14} height={14} />
                </button>
              </div>
            </div>

            <div className="catalog-search-wrap">
              <Search size={11} />
              <input
                type="text"
                placeholder="Type to search"
                value={searchTree}
                onChange={(e) => setSearchTree(e.target.value)}
              />
              <SlidersHorizontal size={11} className="search-filter-icon" />
            </div>

            <div className="catalog-tree-body">
              {/* My organization */}
              <div className="tree-group">
                <button
                  type="button"
                  className="tree-node root-node"
                  onClick={() => toggleNode("org")}
                >
                  <ChevronDown
                    size={12}
                    className={`tree-arrow ${expandedNodes.org ? "" : "collapsed"}`}
                  />
                  <Folder size={13} className="tree-catalog-icon" />
                  <span>Catalogs</span>
                </button>

                {expandedNodes.org && (
                  <div className="tree-children">
                    {tree.map((c) =>
                      renderCatalogNode(c.name, c.name === "main"),
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* Quick History Switcher at bottom of left catalog */}
            <div className="catalog-tree-footer">
              <button
                type="button"
                className="text-link"
                onClick={() => {
                  if (history.data && history.data.length > 0) {
                    handleSqlChange(history.data[0].sql);
                  }
                }}
              >
                <History size={12} />
                Recent queries ({history.data?.length ?? 0})
              </button>
            </div>
          </aside>

          {/* HORIZONTAL SPLITTER RESIZER for Catalog Metadata Pane */}
          <div
            className={`sql-sidebar-resizer ${isDraggingSidebarResizer ? "dragging" : ""}`}
            onMouseDown={startSidebarResize}
            onDoubleClick={() => setSidebarWidth(280)}
            title="Drag to resize catalog pane · Double-click to reset"
          />
        </>
      ) : (
        <aside
          className="sql-side-rail"
          style={{ width: "40px", flexShrink: 0 }}
          aria-label="Catalog sidebar collapsed"
        >
          <button
            type="button"
            className="rail-open-btn"
            title="Open Catalog panel"
            aria-label="Open Catalog panel"
            onClick={() => setSideOpen(true)}
          >
            <SidebarToggleIcon direction="open" width={17} height={17} />
          </button>
        </aside>
      )}

      {/* RIGHT MAIN: Databricks SQL IDE */}
      <section className="sql-main-ide" style={{ flex: 1, minWidth: 0 }}>
        {/* TOP TAB BAR: Query Tabs */}
        <div className="sql-query-tabs-bar">
          <div className="sql-query-tabs">
            {tabs.map((tab) => (
              <div
                key={tab.id}
                className={`sql-query-tab ${tab.id === activeTabId ? "active" : ""}`}
                onClick={() => handleSwitchTab(tab.id)}
              >
                <Terminal size={13} />
                <span className="tab-title">{tab.title}</span>
                {tabs.length > 1 && (
                  <button
                    type="button"
                    className="tab-close-btn"
                    title="Close tab"
                    onClick={(e) => handleCloseTab(e, tab.id)}
                  >
                    <X size={11} />
                  </button>
                )}
              </div>
            ))}
            <button
              type="button"
              className="sql-new-tab-btn"
              title="New query tab"
              onClick={handleAddTab}
            >
              <Plus size={14} />
            </button>
          </div>
        </div>

        {/* SQL ACTION TOOLBAR matching Databricks screenshot */}
        <div className="sql-editor-toolbar">
          <div className="toolbar-left">
            <div className="sql-run-group">
              <button
                type="button"
                className="sql-run-button"
                disabled={!!busy || !sql.trim()}
                onClick={() => void execute(computeId)}
                title="Run query (Ctrl + Enter)"
              >
                {busy === "query" ? (
                  <LoaderCircle size={13} className="spin" />
                ) : (
                  <Play size={13} fill="currentColor" />
                )}
                <span>Run all</span>
              </button>
            </div>

            {busy === "query" ? (
              <div className="sql-run-status running">
                <LoaderCircle size={12} className="spin" />
                <span>Running…</span>
              </div>
            ) : result ? (
              <div
                className="sql-run-status success"
                title={`Query completed in ${result.duration_ms}ms`}
              >
                <Check size={13} strokeWidth={2.5} />
                <span>{formatRuntime(result.duration_ms)}</span>
              </div>
            ) : null}

            {/* Decoupled 1: Catalog Selector */}
            <div className="databricks-dropdown-wrapper" ref={catalogRef}>
              <button
                type="button"
                className="databricks-dropdown-trigger catalog-trigger"
                onClick={() => {
                  setCatalogOpen(!catalogOpen);
                  setSchemaOpen(false);
                }}
                aria-expanded={catalogOpen}
                title="Select Catalog"
              >
                <Database size={12} className="trigger-icon" />
                <span className="trigger-label">{selectedCatalog}</span>
                <ChevronDown
                  size={11}
                  className={`trigger-chevron ${catalogOpen ? "open" : ""}`}
                />
              </button>

              {catalogOpen && (
                <div className="databricks-dropdown-menu panel" role="listbox">
                  <div className="dropdown-menu-header">CATALOGS</div>
                  {Object.keys(CATALOG_SCHEMAS).map((cat) => (
                    <button
                      key={cat}
                      type="button"
                      className={`dropdown-menu-item ${selectedCatalog === cat ? "active" : ""}`}
                      onClick={() => {
                        setSelectedCatalog(cat);
                        setSelectedSchema(CATALOG_SCHEMAS[cat][0] || "default");
                        setCatalogOpen(false);
                      }}
                    >
                      <div className="item-row">
                        <span className="item-title">{cat}</span>
                        {selectedCatalog === cat && (
                          <Check size={13} className="check-icon" />
                        )}
                      </div>
                      <span className="item-sub">
                        {CATALOG_SCHEMAS[cat].length} schemas
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            <span className="context-separator">.</span>

            {/* Decoupled 2: Schema Selector (shows schemas of selected catalog) */}
            <div className="databricks-dropdown-wrapper" ref={schemaRef}>
              <button
                type="button"
                className="databricks-dropdown-trigger schema-trigger"
                onClick={() => {
                  setSchemaOpen(!schemaOpen);
                  setCatalogOpen(false);
                }}
                aria-expanded={schemaOpen}
                title={`Select Schema in ${selectedCatalog}`}
              >
                <Layers size={12} className="trigger-icon" />
                <span className="trigger-label">{selectedSchema}</span>
                <ChevronDown
                  size={11}
                  className={`trigger-chevron ${schemaOpen ? "open" : ""}`}
                />
              </button>

              {schemaOpen && (
                <div className="databricks-dropdown-menu panel" role="listbox">
                  <div className="dropdown-menu-header">
                    SCHEMAS IN {selectedCatalog}
                  </div>
                  {(CATALOG_SCHEMAS[selectedCatalog] || ["default"]).map(
                    (sch) => (
                      <button
                        key={sch}
                        type="button"
                        className={`dropdown-menu-item ${selectedSchema === sch ? "active" : ""}`}
                        onClick={() => {
                          setSelectedSchema(sch);
                          setSchemaOpen(false);
                        }}
                      >
                        <div className="item-row">
                          <span className="item-title">{sch}</span>
                          {selectedSchema === sch && (
                            <Check size={13} className="check-icon" />
                          )}
                        </div>
                      </button>
                    ),
                  )}
                </div>
              )}
            </div>

            <span className="last-edit-info">Last edit {lastSaved}</span>
          </div>

          <div className="toolbar-right">
            <div className="warehouse-picker-wrap">
              <ResourcePicker
                role="sql"
                value={computeId}
                onChange={setComputeId}
              />
            </div>

            <button
              type="button"
              className="toolbar-btn"
              onClick={saveSqlFile}
              title="Download query as a SQL file"
            >
              <Download size={14} />
              <span>Save SQL</span>
            </button>

            {/* Maximize / Restore Editor Button */}
            <button
              type="button"
              className={`toolbar-btn ${maximizedView === "editor" ? "active" : ""}`}
              title={
                maximizedView === "editor"
                  ? "Restore view (show results panel)"
                  : "Maximize editor (expand over results)"
              }
              onClick={() =>
                setMaximizedView(maximizedView === "editor" ? "none" : "editor")
              }
            >
              {maximizedView === "editor" ? (
                <Minimize2 size={13} />
              ) : (
                <Maximize2 size={13} />
              )}
              <span>{maximizedView === "editor" ? "Restore" : "Maximize"}</span>
            </button>
          </div>
        </div>

        {/* MONACO SQL CODE EDITOR */}
        {maximizedView !== "results" && (
          <div
            className="sql-monaco-wrapper"
            style={{
              height:
                maximizedView === "editor"
                  ? "calc(100% - 32px)"
                  : `${editorHeight}px`,
              flex: maximizedView === "editor" ? "1 1 auto" : "none",
            }}
          >
            <Editor
              height="100%"
              language="sql"
              theme={resolved === "dark" ? "substrate-dark" : "substrate-light"}
              beforeMount={configureEditorThemes}
              onMount={(editor) => {
                editorRef.current = editor;
              }}
              value={sql}
              onChange={(v) => handleSqlChange(v || "")}
              options={{
                minimap: { enabled: false },
                fontSize: 12,
                lineNumbers: "on",
                lineHeight: 20,
                padding: { top: 6, bottom: 6 },
                scrollBeyondLastLine: false,
                automaticLayout: true,
              }}
            />
          </div>
        )}

        {/* QUERY PARAMETERS BAR */}
        {maximizedView !== "results" && (
          <div className="sql-params-bar">
            <span className="muted">Ctrl + Enter to run</span>
            {queryId && busy === "query" && (
              <button
                type="button"
                className="cancel-query-btn"
                onClick={() =>
                  void api(`/statements/${queryId}:cancel`, post({})).catch(
                    (e) => setError(String(e)),
                  )
                }
              >
                <Square size={12} />
                Cancel query
              </button>
            )}
          </div>
        )}

        {/* DRAGGABLE RESIZER SPLITTER matching Databricks */}
        {maximizedView === "none" && (
          <div
            className={`sql-editor-resizer ${isDraggingResizer ? "dragging" : ""}`}
            onMouseDown={startResize}
            onDoubleClick={() => setEditorHeight(240)}
            title="Drag up or down to resize query editor and results · Double-click to reset"
          />
        )}

        {/* DATABRICKS RESULTS PANE */}
        {maximizedView !== "editor" && (
          <div
            className={`sql-results-pane ${maximizedView === "results" ? "maximized" : ""}`}
            style={{ flex: 1, minHeight: 0 }}
          >
            <div className="results-pane-tabs-bar">
              <div className="results-tabs">
                <button type="button" className="results-tab active">
                  <TableIcon size={12} className="tab-icon" />
                  <span>Table</span>
                </button>
              </div>

              <div className="results-actions-right">
                <button
                  type="button"
                  className={`results-icon-btn ${maximizedView === "results" ? "active" : ""}`}
                  title={
                    maximizedView === "results"
                      ? "Restore query editor"
                      : "Maximize results (expand over editor)"
                  }
                  onClick={() =>
                    setMaximizedView(
                      maximizedView === "results" ? "none" : "results",
                    )
                  }
                >
                  {maximizedView === "results" ? (
                    <Minimize2 size={13} />
                  ) : (
                    <Maximize2 size={13} />
                  )}
                </button>

                {/* Databricks Export/Download Menu */}
                <div className="databricks-dropdown-wrapper" ref={downloadRef}>
                  <button
                    type="button"
                    className="results-icon-btn"
                    title="Export / Download results"
                    disabled={!result || !!busy}
                    onClick={() => setDownloadOpen(!downloadOpen)}
                  >
                    <ArrowDownToLine size={13} />
                  </button>

                  {downloadOpen && (
                    <div className="databricks-dropdown-menu panel export-menu">
                      <button
                        type="button"
                        className="dropdown-menu-item"
                        onClick={() => {
                          void download("csv", computeId);
                          setDownloadOpen(false);
                        }}
                      >
                        <span className="item-title">Download CSV</span>
                      </button>
                      <button
                        type="button"
                        className="dropdown-menu-item"
                        onClick={() => {
                          void download("arrow", computeId);
                          setDownloadOpen(false);
                        }}
                      >
                        <span className="item-title">Download Arrow</span>
                      </button>
                      <button
                        type="button"
                        className="dropdown-menu-item"
                        onClick={() => {
                          if (result) {
                            const header = result.columns.join("\t");
                            const rows = result.rows
                              .map((r) =>
                                result.columns.map((c) => r[c]).join("\t"),
                              )
                              .join("\n");
                            void navigator.clipboard.writeText(
                              `${header}\n${rows}`,
                            );
                          }
                          setDownloadOpen(false);
                        }}
                      >
                        <span className="item-title">
                          Copy to clipboard (TSV)
                        </span>
                      </button>
                    </div>
                  )}
                </div>
              </div>
            </div>

            <div className="results-content-area">
              {result ? (
                <Grid
                  columns={result.columns}
                  rows={result.rows}
                  showRowNumbers={true}
                />
              ) : (
                <Empty
                  text={
                    busy === "query"
                      ? "Executing query on SQL warehouse…"
                      : "Run a query to inspect its results."
                  }
                />
              )}
            </div>

            {/* DATABRICKS RESULTS FOOTER STATUS BAR */}
            {result && (
              <div className="results-footer-bar">
                <div className="footer-left">
                  <button
                    type="button"
                    className="footer-icon-btn"
                    title="Download results"
                    onClick={() => void download("csv", computeId)}
                  >
                    <ArrowDownToLine size={12} />
                  </button>
                  <span className="footer-metric">
                    <strong>{result.row_count.toLocaleString()}</strong> rows
                  </span>
                  <span className="footer-runtime">
                    {formatRuntime(result.duration_ms)} runtime
                  </span>
                  <span className="footer-metric">
                    {result.files_scanned ?? 0} files scanned
                  </span>
                  {result.truncated && (
                    <span className="footer-metric">Result limit reached</span>
                  )}
                </div>
                <div className="footer-right">
                  <span className="refreshed-timestamp">
                    Refreshed just now
                  </span>
                </div>
              </div>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
