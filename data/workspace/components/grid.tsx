"use client";
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  Filter,
  Download,
  Columns3,
  MoreVertical,
  Pin,
  X,
} from "lucide-react";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

export type Row = Record<string, unknown>;

export function cell(value: unknown): string {
  return value == null
    ? "NULL"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
}

export type FilterOperator =
  | "equals"
  | "not_equals"
  | "is_one_of"
  | "is_not_one_of"
  | "contains"
  | "does_not_contain";

export interface DatabricksFilter {
  id: string;
  column: string;
  operator: FilterOperator;
  values: string[];
  enabled: boolean;
}

export const OPERATOR_LABELS: Record<FilterOperator, string> = {
  equals: "Is equal to",
  not_equals: "Is not equal to",
  is_one_of: "Is one of",
  is_not_one_of: "Is not one of",
  contains: "Contains",
  does_not_contain: "Does not contain",
};

function inferColumnType(
  name: string,
  rows: Row[],
): "number" | "string" | "date" | "boolean" {
  for (const row of rows.slice(0, 15)) {
    const val = row[name];
    if (val != null) {
      if (typeof val === "boolean") return "boolean";
      if (typeof val === "number") return "number";
      if (typeof val === "string") {
        if (/^\d{4}-\d{2}-\d{2}/.test(val)) return "date";
        return "string";
      }
    }
  }
  return "string";
}

function estimateColumnWidth(name: string, rows: Row[]): number {
  let maxLen = name.length;
  const sample = rows.slice(0, 30);
  for (const r of sample) {
    const val = r[name];
    if (val != null) {
      const str = typeof val === "object" ? JSON.stringify(val) : String(val);
      if (str.length > maxLen) {
        maxLen = Math.min(str.length, 45);
      }
    }
  }
  return Math.max(90, Math.min(360, Math.round(maxLen * 8.5 + 55)));
}

interface SelectionContextMenuState {
  x: number;
  y: number;
  clickedValue: string;
  clickedColumn: string;
}

export function Grid({
  columns,
  rows,
  onSort,
  sortBy,
  showRowNumbers = true,
  filterText = "",
}: {
  columns: string[];
  rows: Row[];
  onSort?: (name: string) => void;
  sortBy?: { column: string; descending: boolean };
  showRowNumbers?: boolean;
  filterText?: string;
}) {
  "use no memo";

  // Sorting state (handled directly via table headers / sort arrow)
  const [localSorting, setSorting] = useState<SortingState>([]);
  const sorting: SortingState = sortBy?.column
    ? [{ id: sortBy.column, desc: sortBy.descending }]
    : localSorting;
  const [localSearch, setLocalSearch] = useState("");
  const [hiddenColumns, setHiddenColumns] = useState<Set<string>>(new Set());
  const [formattedColumns, setFormattedColumns] = useState<Set<string>>(
    new Set(),
  );

  // Column header 3-dots context menu state (NO sorting items inside)
  const [activeMenuCol, setActiveMenuCol] = useState<string | null>(null);
  const [menuPos, setMenuPos] = useState<{ x: number; y: number } | null>(null);

  // Databricks structured filters state
  const [filters, updateFilters] = useState<DatabricksFilter[]>([]);
  const setFilters: React.Dispatch<React.SetStateAction<DatabricksFilter[]>> = (
    value,
  ) => {
    setSelectedRowIndices(new Set());
    setAnchorIndex(null);
    updateFilters(value);
  };
  const [editingFilterId, setEditingFilterId] = useState<string | null>(null);
  const [filterPopoverPos, setFilterPopoverPos] = useState<{
    x: number;
    y: number;
  } | null>(null);

  // Filter popover dropdown selectors
  const [colDropdownOpen, setColDropdownOpen] = useState(false);
  const [opDropdownOpen, setOpDropdownOpen] = useState(false);
  const [valDropdownOpen, setValDropdownOpen] = useState(false);
  const [valSearchText, setValSearchText] = useState("");

  // Pinned columns set
  const [pinnedCols, setPinnedCols] = useState<Set<string>>(new Set());

  // Row and Cell Selection state
  const [selectedRowIndices, setSelectedRowIndices] = useState<Set<number>>(
    new Set(),
  );
  const [anchorIndex, setAnchorIndex] = useState<number | null>(null);
  const [selectionMenu, setSelectionMenu] =
    useState<SelectionContextMenuState | null>(null);
  const [copyAsSubmenuOpen, setCopyAsSubmenuOpen] = useState(false);

  // Toast feedback notification
  const [toastMessage, setToastMessage] = useState<string | null>(null);
  const toastTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  const showToast = useCallback((msg: string) => {
    if (toastTimeoutRef.current) clearTimeout(toastTimeoutRef.current);
    setToastMessage(msg);
    toastTimeoutRef.current = setTimeout(() => {
      setToastMessage(null);
    }, 2200);
  }, []);

  const copyToClipboard = useCallback(
    async (text: string, label: string) => {
      try {
        await navigator.clipboard.writeText(text);
        showToast(`Copied ${label}`);
      } catch {
        showToast("Failed to copy");
      }
    },
    [showToast],
  );

  useEffect(
    () => () => {
      if (toastTimeoutRef.current) clearTimeout(toastTimeoutRef.current);
    },
    [],
  );

  // Close menus on outside click or escape
  useEffect(() => {
    const handleGlobalClick = () => {
      setActiveMenuCol(null);
      setMenuPos(null);
      setSelectionMenu(null);
      setColDropdownOpen(false);
      setOpDropdownOpen(false);
      setValDropdownOpen(false);
    };
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setActiveMenuCol(null);
        setMenuPos(null);
        setSelectionMenu(null);
        setEditingFilterId(null);
        setColDropdownOpen(false);
        setOpDropdownOpen(false);
        setValDropdownOpen(false);
      }
    };
    window.addEventListener("click", handleGlobalClick);
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("click", handleGlobalClick);
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, []);

  // Filter rows by global filterText AND Databricks structured filters
  const filteredRows = useMemo(() => {
    let result = rows;

    // 1. Global text filter
    if (filterText.trim()) {
      const lower = filterText.toLowerCase();
      result = result.filter((r) =>
        columns.some((c) =>
          String(r[c] ?? "")
            .toLowerCase()
            .includes(lower),
        ),
      );
    }

    if (localSearch.trim()) {
      const term = localSearch.toLowerCase();
      result = result.filter((row) =>
        columns.some((column) =>
          cell(row[column]).toLowerCase().includes(term),
        ),
      );
    }
    // 2. Structured Databricks filters
    for (const f of filters) {
      if (!f.enabled || f.values.length === 0) continue;
      result = result.filter((r) => {
        const val = r[f.column];
        const strVal = val == null ? "" : String(val);
        const lowerVal = strVal.toLowerCase();

        switch (f.operator) {
          case "equals":
            return f.values.some((v) => lowerVal === v.toLowerCase());
          case "not_equals":
            return !f.values.some((v) => lowerVal === v.toLowerCase());
          case "is_one_of":
            return f.values.some((v) => lowerVal === v.toLowerCase());
          case "is_not_one_of":
            return !f.values.some((v) => lowerVal === v.toLowerCase());
          case "contains":
            return f.values.some((v) => lowerVal.includes(v.toLowerCase()));
          case "does_not_contain":
            return !f.values.some((v) => lowerVal.includes(v.toLowerCase()));
          default:
            return true;
        }
      });
    }

    return result;
  }, [rows, columns, filterText, localSearch, filters]);

  const orderedColumns = useMemo(() => {
    const pinned: string[] = [];
    const normal: string[] = [];
    for (const c of columns) {
      if (hiddenColumns.has(c)) continue;
      if (pinnedCols.has(c)) {
        pinned.push(c);
      } else {
        normal.push(c);
      }
    }
    return [...pinned, ...normal];
  }, [columns, pinnedCols, hiddenColumns]);

  // Keyboard shortcut Ctrl+C / Cmd+C for selected rows
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "c") {
        if (
          e.target instanceof HTMLInputElement ||
          e.target instanceof HTMLTextAreaElement ||
          (e.target instanceof HTMLElement && e.target.isContentEditable)
        )
          return;
        if (selectedRowIndices.size > 0 && !window.getSelection()?.toString()) {
          e.preventDefault();
          const sortedIndices = Array.from(selectedRowIndices).sort(
            (a, b) => a - b,
          );
          const selectedRows = sortedIndices
            .map((i) => filteredRows[i])
            .filter(Boolean);
          if (selectedRows.length > 0) {
            const header = orderedColumns.join("\t");
            const dataLines = selectedRows.map((r) =>
              orderedColumns.map((c) => cell(r[c])).join("\t"),
            );
            void copyToClipboard(
              `${header}\n${dataLines.join("\n")}`,
              `${selectedRows.length} selected row${selectedRows.length > 1 ? "s" : ""}`,
            );
          }
        }
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [selectedRowIndices, filteredRows, orderedColumns, copyToClipboard]);

  // Order columns according to pinned status (pinned columns first)

  // Row selection handler
  const handleRowClick = useCallback(
    (rowIndex: number, e: React.MouseEvent, order: number[]) => {
      if (e.shiftKey && anchorIndex !== null) {
        // Shift range selection
        const first = order.indexOf(anchorIndex),
          last = order.indexOf(rowIndex);
        const newSet = new Set(
          order.slice(Math.min(first, last), Math.max(first, last) + 1),
        );
        setSelectedRowIndices(newSet);
      } else if (e.ctrlKey || e.metaKey) {
        // Ctrl multi-select toggle
        setSelectedRowIndices((prev) => {
          const next = new Set(prev);
          if (next.has(rowIndex)) {
            next.delete(rowIndex);
          } else {
            next.add(rowIndex);
          }
          return next;
        });
        setAnchorIndex(rowIndex);
      } else {
        // Single row select
        setSelectedRowIndices(new Set([rowIndex]));
        setAnchorIndex(rowIndex);
      }
    },
    [anchorIndex],
  );

  const definitions = useMemo<ColumnDef<Row>[]>(() => {
    const defs: ColumnDef<Row>[] = [];

    if (showRowNumbers) {
      defs.push({
        id: "__row_index__",
        header: "",
        size: 38,
        enableResizing: false,
        cell: (info) => {
          const isSelected = selectedRowIndices.has(info.row.index);
          return (
            <div
              className={`grid-row-index-wrap ${isSelected ? "selected-index" : ""}`}
              onClick={(e) =>
                handleRowClick(
                  info.row.index,
                  e,
                  info.table.getRowModel().rows.map((row) => row.index),
                )
              }
            >
              <span className="grid-row-index">{info.row.index + 1}</span>
            </div>
          );
        },
      });
    }

    for (const name of orderedColumns) {
      const type = inferColumnType(name, rows);
      const estimatedSize = estimateColumnWidth(name, rows);

      defs.push({
        id: name,
        accessorFn: (row) => row[name],
        header: name,
        size: estimatedSize,
        cell: (info) => {
          const rawVal = info.getValue();
          const strVal =
            formattedColumns.has(name) && typeof rawVal === "number"
              ? new Intl.NumberFormat(undefined, {
                  maximumFractionDigits: 6,
                }).format(rawVal)
              : cell(rawVal);
          const isSelected = selectedRowIndices.has(info.row.index);

          return (
            <div
              className={`grid-cell-wrapper ${type === "number" ? "align-right" : ""} ${isSelected ? "cell-selected" : ""}`}
              onClick={(e) =>
                handleRowClick(
                  info.row.index,
                  e,
                  info.table.getRowModel().rows.map((row) => row.index),
                )
              }
              onContextMenu={(e) => {
                e.preventDefault();
                e.stopPropagation();
                if (!selectedRowIndices.has(info.row.index)) {
                  setSelectedRowIndices(new Set([info.row.index]));
                  setAnchorIndex(info.row.index);
                }
                setSelectionMenu({
                  x: Math.min(e.clientX, window.innerWidth - 240),
                  y: Math.min(e.clientY, window.innerHeight - 240),
                  clickedValue: strVal,
                  clickedColumn: name,
                });
                setActiveMenuCol(null);
                setEditingFilterId(null);
              }}
              title={strVal}
            >
              <span
                className={`cell-value ${rawVal == null ? "cell-null" : ""}`}
              >
                {strVal}
              </span>
            </div>
          );
        },
        meta: { type, isPinned: pinnedCols.has(name) },
      });
    }

    return defs;
  }, [
    orderedColumns,
    rows,
    showRowNumbers,
    pinnedCols,
    selectedRowIndices,
    anchorIndex,
    formattedColumns,
    handleRowClick,
  ]);

  const table = useReactTable({
    data: filteredRows,
    columns: definitions,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    columnResizeMode: "onChange",
    manualSorting: !!onSort,
  });

  const parent = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: filteredRows.length,
    getScrollElement: () => parent.current,
    estimateSize: () => 28,
    overscan: 8,
  });

  const virtualRows = virtualizer.getVirtualItems();
  const paddingTop = virtualRows[0]?.start ?? 0;
  const paddingBottom = virtualRows.length
    ? virtualizer.getTotalSize() - virtualRows[virtualRows.length - 1].end
    : 0;
  const totalColumns = orderedColumns.length + (showRowNumbers ? 1 : 0);

  const toggleSort = (colId: string) => {
    if (onSort) {
      setSorting((previous) => [
        {
          id: colId,
          desc: previous[0]?.id === colId ? !previous[0].desc : false,
        },
      ]);
      onSort(colId);
      return;
    }
    setSorting((prev) => {
      const existing = prev.find((s) => s.id === colId);
      if (!existing) {
        return [{ id: colId, desc: false }];
      }
      if (!existing.desc) {
        return [{ id: colId, desc: true }];
      }
      return [];
    });
  };

  const handleOpenColMenu = (e: React.MouseEvent, colId: string) => {
    e.stopPropagation();
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
    if (activeMenuCol === colId) {
      setActiveMenuCol(null);
      setMenuPos(null);
    } else {
      setActiveMenuCol(colId);
      setMenuPos({
        x: Math.min(rect.left, window.innerWidth - 210),
        y: rect.bottom + 4,
      });
      setSelectionMenu(null);
      setEditingFilterId(null);
    }
  };

  const handleCopyColumnData = (colName: string) => {
    const values = filteredRows.map((r) => cell(r[colName])).join("\n");
    void copyToClipboard(values, `all data in column "${colName}"`);
    setActiveMenuCol(null);
  };

  const togglePinColumn = (colName: string) => {
    setPinnedCols((prev) => {
      const next = new Set(prev);
      if (next.has(colName)) {
        next.delete(colName);
        showToast(`Unpinned ${colName}`);
      } else {
        next.add(colName);
        showToast(`Pinned ${colName}`);
      }
      return next;
    });
    setActiveMenuCol(null);
  };

  // Filter creation & opening
  const handleAddFilter = (columnName?: string) => {
    const targetCol = columnName || columns[0] || "";
    const newFilter: DatabricksFilter = {
      id: `filter-${Date.now()}`,
      column: targetCol,
      operator: "is_one_of",
      values: [],
      enabled: true,
    };
    setFilters((prev) => [...prev, newFilter]);
    setEditingFilterId(newFilter.id);
    setActiveMenuCol(null);
  };

  const handleRemoveFilter = (filterId: string) => {
    setFilters((prev) => prev.filter((f) => f.id !== filterId));
    if (editingFilterId === filterId) {
      setEditingFilterId(null);
    }
  };

  // Distinct values for currently edited filter column
  const activeEditingFilter = filters.find((f) => f.id === editingFilterId);
  const distinctValuesForActiveFilter = useMemo(() => {
    if (!activeEditingFilter) return [];
    const col = activeEditingFilter.column;
    const set = new Set<string>();
    for (const r of rows) {
      const val = r[col];
      if (val != null) {
        set.add(String(val));
      }
    }
    const list = Array.from(set);
    if (!valSearchText.trim()) return list.slice(0, 50);
    return list
      .filter((v) => v.toLowerCase().includes(valSearchText.toLowerCase()))
      .slice(0, 50);
  }, [activeEditingFilter, rows, valSearchText]);

  // Copy formatting options from selection menu
  const copySelectedRowsFormatted = (
    format: "tsv" | "csv" | "markdown" | "json",
  ) => {
    const sortedIndices = Array.from(selectedRowIndices).sort((a, b) => a - b);
    const selRows = sortedIndices.map((i) => filteredRows[i]).filter(Boolean);
    if (selRows.length === 0) return;

    if (format === "json") {
      void copyToClipboard(
        JSON.stringify(selRows, null, 2),
        `${selRows.length} rows as JSON`,
      );
    } else if (format === "csv") {
      const header = orderedColumns
        .map((c) => `"${c.replace(/"/g, '""')}"`)
        .join(",");
      const lines = selRows.map((r) =>
        orderedColumns
          .map((c) => {
            const val = cell(r[c]);
            return `"${val.replace(/"/g, '""')}"`;
          })
          .join(","),
      );
      void copyToClipboard(
        `${header}\n${lines.join("\n")}`,
        `${selRows.length} rows as CSV`,
      );
    } else if (format === "markdown") {
      const header = `| ${orderedColumns.join(" | ")} |`;
      const sep = `| ${orderedColumns.map(() => "---").join(" | ")} |`;
      const lines = selRows.map(
        (r) => `| ${orderedColumns.map((c) => cell(r[c])).join(" | ")} |`,
      );
      void copyToClipboard(
        `${header}\n${sep}\n${lines.join("\n")}`,
        `${selRows.length} rows as Markdown`,
      );
    } else {
      // TSV plain text
      const header = orderedColumns.join("\t");
      const lines = selRows.map((r) =>
        orderedColumns.map((c) => cell(r[c])).join("\t"),
      );
      void copyToClipboard(
        `${header}\n${lines.join("\n")}`,
        `${selRows.length} rows as Plain text`,
      );
    }
    setSelectionMenu(null);
  };

  return (
    <div className="databricks-grid-container">
      {/* Toast Feedback Notification */}
      {toastMessage && (
        <div className="databricks-toast">
          <Check size={12} className="toast-icon" />
          <span>{toastMessage}</span>
        </div>
      )}

      <div className="result-grid-toolbar">
        <label>
          <span className="sr-only">Search loaded rows</span>
          <input
            aria-label="Search loaded rows"
            placeholder="Search loaded rows…"
            value={localSearch}
            onChange={(event) => {
              setSelectedRowIndices(new Set());
              setLocalSearch(event.target.value);
            }}
          />
        </label>
        <span className="muted">
          {filteredRows.length.toLocaleString()} of{" "}
          {rows.length.toLocaleString()} loaded rows
          {selectedRowIndices.size
            ? ` · ${selectedRowIndices.size} selected`
            : ""}
        </span>
        <div className="result-grid-actions">
          <details className="grid-column-picker">
            <summary>
              <Columns3 size={14} /> Columns
            </summary>
            <div>
              {columns.map((column) => (
                <label key={column}>
                  <input
                    type="checkbox"
                    checked={!hiddenColumns.has(column)}
                    disabled={
                      !hiddenColumns.has(column) && orderedColumns.length === 1
                    }
                    onChange={() =>
                      setHiddenColumns((previous) => {
                        const next = new Set(previous);
                        if (next.has(column)) next.delete(column);
                        else next.add(column);
                        return next;
                      })
                    }
                  />
                  {column}
                </label>
              ))}
            </div>
          </details>
          {selectedRowIndices.size > 0 && (
            <button
              onClick={() => {
                setSelectedRowIndices(new Set());
                setAnchorIndex(null);
              }}
            >
              Clear selection
            </button>
          )}
          <button
            disabled={!filteredRows.length}
            onClick={() => {
              const records = table
                .getRowModel()
                .rows.filter(
                  (row) =>
                    !selectedRowIndices.size ||
                    selectedRowIndices.has(row.index),
                )
                .map((row) => row.original);
              const quote = (value: unknown) => {
                const raw = cell(value);
                return (
                  '"' +
                  (
                    (typeof value === "string" && /^[=+@-]/.test(raw)
                      ? "'"
                      : "") + raw
                  ).replaceAll('"', '""') +
                  '"'
                );
              };
              const csv = [
                orderedColumns.map(quote).join(","),
                ...records.map((row) =>
                  orderedColumns.map((column) => quote(row[column])).join(","),
                ),
              ].join("\r\n");
              const link = document.createElement("a");
              link.href = URL.createObjectURL(
                new Blob([csv], { type: "text/csv;charset=utf-8" }),
              );
              link.download = "loaded-rows.csv";
              link.click();
              URL.revokeObjectURL(link.href);
            }}
          >
            <Download size={14} /> Download{" "}
            {selectedRowIndices.size ? "selected" : "loaded"} rows
          </button>
        </div>
      </div>
      {/* Filters apply to the loaded result, never silently to the entire dataset. */}
      <div className="databricks-filter-bar">
        {filters.map((f) => {
          const valPreview =
            f.values.length > 0
              ? f.values.length === 1
                ? `"${f.values[0]}"`
                : `${f.values.length} selected`
              : "any";
          return (
            <div
              key={f.id}
              className={`filter-chip ${f.enabled ? "enabled" : "disabled"} ${editingFilterId === f.id ? "active-editing" : ""}`}
            >
              <button
                type="button"
                className="chip-label-btn"
                onClick={(e) => {
                  e.stopPropagation();
                  setEditingFilterId(editingFilterId === f.id ? null : f.id);
                  const rect = (
                    e.currentTarget as HTMLElement
                  ).getBoundingClientRect();
                  setFilterPopoverPos({ x: rect.left, y: rect.bottom + 4 });
                }}
              >
                <strong>{f.column}</strong>
                <span>{OPERATOR_LABELS[f.operator].toLowerCase()}</span>
                <span className="chip-val-preview">{valPreview}</span>
              </button>
              <button
                type="button"
                className="chip-remove-btn"
                title="Remove filter"
                onClick={(e) => {
                  e.stopPropagation();
                  handleRemoveFilter(f.id);
                }}
              >
                <X size={11} />
              </button>
            </div>
          );
        })}

        <button
          type="button"
          className="add-filter-btn"
          onClick={(e) => {
            e.stopPropagation();
            const rect = (
              e.currentTarget as HTMLElement
            ).getBoundingClientRect();
            setFilterPopoverPos({ x: rect.left, y: rect.bottom + 4 });
            handleAddFilter();
          }}
        >
          <Filter size={11} className="filter-add-icon" />
          <span>Add filter</span>
        </button>
      </div>

      {/* DATABRICKS FILTER POPOVER (Modal matching screenshots 3, 4, 5) */}
      {activeEditingFilter && filterPopoverPos && (
        <div
          className="databricks-filter-popover"
          style={{
            top: `${filterPopoverPos.y}px`,
            left: `${filterPopoverPos.x}px`,
          }}
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header Row: Enabled Toggle Switch */}
          <div className="filter-popover-top-row">
            <label className="filter-toggle-label">
              <input
                type="checkbox"
                checked={activeEditingFilter.enabled}
                onChange={(e) => {
                  const val = e.target.checked;
                  setFilters((prev) =>
                    prev.map((f) =>
                      f.id === activeEditingFilter.id
                        ? { ...f, enabled: val }
                        : f,
                    ),
                  );
                }}
              />
              <span className="filter-toggle-switch" />
              <span className="toggle-text">Enabled</span>
            </label>

            <button
              type="button"
              className="popover-close-btn"
              onClick={() => setEditingFilterId(null)}
            >
              <X size={12} />
            </button>
          </div>

          {/* Field 1: Column Selector Dropdown */}
          <div className="filter-field-wrapper">
            <button
              type="button"
              className="filter-select-trigger"
              onClick={() => {
                setColDropdownOpen(!colDropdownOpen);
                setOpDropdownOpen(false);
                setValDropdownOpen(false);
              }}
            >
              <span className="trigger-val">
                <span className="column-type-badge small">
                  {inferColumnType(activeEditingFilter.column, rows) ===
                  "number"
                    ? "#"
                    : "Abc"}
                </span>
                {activeEditingFilter.column}
              </span>
              <ChevronDown size={12} />
            </button>

            {colDropdownOpen && (
              <div className="filter-dropdown-menu">
                {columns.map((c) => (
                  <button
                    key={c}
                    type="button"
                    className={`filter-dropdown-item ${activeEditingFilter.column === c ? "selected" : ""}`}
                    onClick={() => {
                      setFilters((prev) =>
                        prev.map((f) =>
                          f.id === activeEditingFilter.id
                            ? { ...f, column: c, values: [] }
                            : f,
                        ),
                      );
                      setColDropdownOpen(false);
                    }}
                  >
                    <span className="column-type-badge small">
                      {inferColumnType(c, rows) === "number" ? "#" : "Abc"}
                    </span>
                    <span>{c}</span>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Field 2: Operator Selector Dropdown */}
          <div className="filter-field-wrapper">
            <button
              type="button"
              className="filter-select-trigger"
              onClick={() => {
                setOpDropdownOpen(!opDropdownOpen);
                setColDropdownOpen(false);
                setValDropdownOpen(false);
              }}
            >
              <span className="trigger-val">
                {OPERATOR_LABELS[activeEditingFilter.operator]}
              </span>
              <ChevronDown size={12} />
            </button>

            {opDropdownOpen && (
              <div className="filter-dropdown-menu">
                {(Object.keys(OPERATOR_LABELS) as FilterOperator[]).map(
                  (op) => (
                    <button
                      key={op}
                      type="button"
                      className={`filter-dropdown-item ${activeEditingFilter.operator === op ? "selected" : ""}`}
                      onClick={() => {
                        setFilters((prev) =>
                          prev.map((f) =>
                            f.id === activeEditingFilter.id
                              ? { ...f, operator: op }
                              : f,
                          ),
                        );
                        setOpDropdownOpen(false);
                      }}
                    >
                      {activeEditingFilter.operator === op && (
                        <Check size={11} className="check-icon" />
                      )}
                      <span>{OPERATOR_LABELS[op]}</span>
                    </button>
                  ),
                )}
              </div>
            )}
          </div>

          {/* Field 3: Value Selector / Multi-Select with Checkboxes */}
          <div className="filter-field-wrapper">
            <div
              className="filter-multiselect-box"
              onClick={() => {
                setValDropdownOpen(!valDropdownOpen);
                setColDropdownOpen(false);
                setOpDropdownOpen(false);
              }}
            >
              <div className="selected-tags">
                {activeEditingFilter.values.length === 0 ? (
                  <span className="placeholder-text">Type or select</span>
                ) : (
                  activeEditingFilter.values.map((v) => (
                    <span key={v} className="filter-val-tag">
                      <span>{v}</span>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          setFilters((prev) =>
                            prev.map((f) =>
                              f.id === activeEditingFilter.id
                                ? {
                                    ...f,
                                    values: f.values.filter(
                                      (item) => item !== v,
                                    ),
                                  }
                                : f,
                            ),
                          );
                        }}
                      >
                        <X size={10} />
                      </button>
                    </span>
                  ))
                )}
              </div>
              <ChevronDown size={12} className="multiselect-arrow" />
            </div>

            {valDropdownOpen && (
              <div
                className="filter-values-dropdown"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="val-search-wrap">
                  <input
                    type="text"
                    placeholder="Search values..."
                    value={valSearchText}
                    onChange={(e) => setValSearchText(e.target.value)}
                    autoFocus
                  />
                </div>
                <div className="val-checkboxes-list">
                  {distinctValuesForActiveFilter.map((v) => {
                    const isChecked = activeEditingFilter.values.includes(v);
                    return (
                      <label key={v} className="val-checkbox-item">
                        <input
                          type="checkbox"
                          checked={isChecked}
                          onChange={() => {
                            setFilters((prev) =>
                              prev.map((f) => {
                                if (f.id !== activeEditingFilter.id) return f;
                                const nextVals = isChecked
                                  ? f.values.filter((x) => x !== v)
                                  : [...f.values, v];
                                return { ...f, values: nextVals };
                              }),
                            );
                          }}
                        />
                        <span className="checkbox-val-text">{v}</span>
                      </label>
                    );
                  })}
                  {distinctValuesForActiveFilter.length === 0 && (
                    <div className="empty-vals-notice">No values found</div>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Bottom link: + Add OR condition */}
          <div className="filter-popover-bottom">
            <button
              type="button"
              className="add-or-condition-btn"
              onClick={() => {
                handleAddFilter(activeEditingFilter.column);
              }}
            >
              + Add filter (AND)
            </button>
          </div>
        </div>
      )}

      {/* Floating Column Header 3-Dots Context Menu (EXACT Databricks options, NO duplicate sorting!) */}
      {activeMenuCol && menuPos && (
        <div
          className="databricks-column-context-menu"
          style={{ top: `${menuPos.y}px`, left: `${menuPos.x}px` }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            type="button"
            className="col-menu-item"
            onClick={() => {
              void copyToClipboard(
                activeMenuCol,
                `column name "${activeMenuCol}"`,
              );
              setActiveMenuCol(null);
            }}
          >
            <Copy size={12} />
            <span>Copy column name</span>
          </button>

          <button
            type="button"
            className="col-menu-item"
            onClick={() => {
              handleAddFilter(activeMenuCol);
              setActiveMenuCol(null);
            }}
          >
            <Filter size={12} />
            <span>Filter</span>
          </button>

          <button
            type="button"
            className="col-menu-item"
            disabled={inferColumnType(activeMenuCol, rows) !== "number"}
            onClick={() => {
              setFormattedColumns((previous) => {
                const next = new Set(previous);
                if (next.has(activeMenuCol)) next.delete(activeMenuCol);
                else next.add(activeMenuCol);
                return next;
              });
              setActiveMenuCol(null);
            }}
          >
            <ChevronRight size={12} />
            <span>
              {formattedColumns.has(activeMenuCol)
                ? "Show raw values"
                : "Format numbers"}
            </span>
          </button>

          <button
            type="button"
            className="col-menu-item"
            onClick={() => togglePinColumn(activeMenuCol)}
          >
            <Pin size={12} />
            <span>
              {pinnedCols.has(activeMenuCol) ? "Unpin column" : "Pin column"}
            </span>
          </button>

          <button
            type="button"
            className="col-menu-item"
            onClick={() => handleCopyColumnData(activeMenuCol)}
          >
            <Copy size={12} />
            <span>Copy column values</span>
          </button>
        </div>
      )}

      {/* DATABRICKS CELL / SELECTION RIGHT-CLICK CONTEXT MENU (Matching screenshot 2) */}
      {selectionMenu && (
        <div
          className="databricks-selection-context-menu"
          style={{ top: `${selectionMenu.y}px`, left: `${selectionMenu.x}px` }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            type="button"
            className="col-menu-item"
            onClick={() => copySelectedRowsFormatted("tsv")}
          >
            <Copy size={12} />
            <span>Copy</span>
            <span className="shortcut-badge">Ctrl + C</span>
          </button>

          <div
            className="col-menu-item has-submenu"
            onMouseEnter={() => setCopyAsSubmenuOpen(true)}
            onMouseLeave={() => setCopyAsSubmenuOpen(false)}
          >
            <div className="submenu-trigger-row">
              <span className="item-title-left">
                <Copy size={12} />
                <span>Copy as</span>
              </span>
              <ChevronRight size={12} />
            </div>

            {copyAsSubmenuOpen && (
              <div className="databricks-sub-menu">
                <button
                  type="button"
                  className="col-menu-item"
                  onClick={() => copySelectedRowsFormatted("tsv")}
                >
                  Plain text
                </button>
                <button
                  type="button"
                  className="col-menu-item"
                  onClick={() => copySelectedRowsFormatted("csv")}
                >
                  CSV
                </button>
                <button
                  type="button"
                  className="col-menu-item"
                  onClick={() => copySelectedRowsFormatted("markdown")}
                >
                  Markdown
                </button>
                <button
                  type="button"
                  className="col-menu-item"
                  onClick={() => copySelectedRowsFormatted("json")}
                >
                  JSON
                </button>
              </div>
            )}
          </div>

          <button
            type="button"
            className="col-menu-item"
            onClick={() => {
              void copyToClipboard(columns.join("\t"), "column names");
              setSelectionMenu(null);
            }}
          >
            <span>Copy column names</span>
          </button>

          <div className="menu-divider" />

          <button
            type="button"
            className="col-menu-item"
            onClick={() => {
              const newFilter: DatabricksFilter = {
                id: `filter-${Date.now()}`,
                column: selectionMenu.clickedColumn,
                operator: "equals",
                values: [selectionMenu.clickedValue],
                enabled: true,
              };
              setFilters((prev) => [...prev, newFilter]);
              showToast(
                `Filtered ${selectionMenu.clickedColumn} = "${selectionMenu.clickedValue}"`,
              );
              setSelectionMenu(null);
            }}
          >
            <span>Filter by this value</span>
          </button>

          <button
            type="button"
            className="col-menu-item"
            onClick={() => {
              const newFilter: DatabricksFilter = {
                id: `filter-${Date.now()}`,
                column: selectionMenu.clickedColumn,
                operator: "not_equals",
                values: [selectionMenu.clickedValue],
                enabled: true,
              };
              setFilters((prev) => [...prev, newFilter]);
              showToast(
                `Excluded ${selectionMenu.clickedColumn} = "${selectionMenu.clickedValue}"`,
              );
              setSelectionMenu(null);
            }}
          >
            <span>Exclude this value</span>
          </button>
        </div>
      )}

      {/* Grid Table */}
      <div className="grid-scroll databricks-grid-scroll" ref={parent}>
        <table className="databricks-table">
          <thead>
            {table.getHeaderGroups().map((group) => (
              <tr key={group.id}>
                {group.headers.map((header) => {
                  const isIndex = header.id === "__row_index__";
                  const type = (
                    header.column.columnDef as {
                      meta?: { type: string; isPinned?: boolean };
                    }
                  )?.meta?.type;
                  const isPinned = (
                    header.column.columnDef as {
                      meta?: { type: string; isPinned?: boolean };
                    }
                  )?.meta?.isPinned;

                  const sortState = sorting.find((s) => s.id === header.id);
                  const hasActiveFilter = filters.some(
                    (f) =>
                      f.enabled &&
                      f.column === header.id &&
                      f.values.length > 0,
                  );

                  return (
                    <th
                      key={header.id}
                      className={`${isIndex ? "grid-index-header" : ""} ${isPinned ? "pinned-header" : ""}`}
                      style={{
                        width: isIndex ? 38 : header.getSize(),
                        minWidth: isIndex ? 38 : header.getSize(),
                        position: isPinned || isIndex ? "sticky" : "relative",
                        left: isIndex
                          ? 0
                          : isPinned
                            ? (showRowNumbers ? 38 : 0) +
                              orderedColumns
                                .filter((name) => pinnedCols.has(name))
                                .slice(
                                  0,
                                  orderedColumns
                                    .filter((name) => pinnedCols.has(name))
                                    .indexOf(header.id),
                                )
                                .reduce(
                                  (sum, name) =>
                                    sum +
                                    (table.getColumn(name)?.getSize() ?? 0),
                                  0,
                                )
                            : undefined,
                        zIndex: isPinned || isIndex ? 4 : undefined,
                        background: "var(--subtle)",
                      }}
                    >
                      {!isIndex ? (
                        <div className="databricks-header-content">
                          {/* Data Type Badge: Abc, #, 📅, TF */}
                          <span className="column-type-badge">
                            {type === "number" ? (
                              "#"
                            ) : type === "date" ? (
                              "📅"
                            ) : type === "boolean" ? (
                              "TF"
                            ) : (
                              <span className="type-abc">
                                <sup>A</sup>bc
                              </span>
                            )}
                          </span>

                          {/* Column Name with direct sort on click */}
                          <span
                            className="column-label-text"
                            title={`Sort by ${header.id}`}
                            onClick={() => toggleSort(header.id)}
                          >
                            {flexRender(
                              header.column.columnDef.header,
                              header.getContext(),
                            )}
                          </span>

                          {/* Filter active badge */}
                          {hasActiveFilter && (
                            <span
                              className="column-filter-active-indicator"
                              title={`Column has active filter`}
                              onClick={(e) => {
                                e.stopPropagation();
                                const f = filters.find(
                                  (x) => x.column === header.id,
                                );
                                if (f) setEditingFilterId(f.id);
                              }}
                            >
                              <Filter size={10} />
                            </span>
                          )}

                          {/* Sort Trigger Arrow */}
                          <button
                            type="button"
                            className={`header-action-btn sort-btn ${sortState ? "active" : ""}`}
                            aria-label={`Sort ${header.id}`}
                            title={
                              sortState?.desc
                                ? "Sorted descending · Click to unsort"
                                : sortState
                                  ? "Sorted ascending · Click for descending"
                                  : "Click to sort"
                            }
                            onClick={(e) => {
                              e.stopPropagation();
                              toggleSort(header.id);
                            }}
                          >
                            {sortState?.desc ? (
                              <ArrowDown size={11} className="active-arrow" />
                            ) : sortState ? (
                              <ArrowUp size={11} className="active-arrow" />
                            ) : (
                              <ArrowUpDown size={11} className="idle-arrow" />
                            )}
                          </button>

                          {/* Context Menu Trigger ⋮ */}
                          <button
                            type="button"
                            className={`header-action-btn menu-btn ${activeMenuCol === header.id ? "active" : ""}`}
                            aria-label={`Column options for ${header.id}`}
                            title="Column options"
                            onClick={(e) => handleOpenColMenu(e, header.id)}
                          >
                            <MoreVertical size={12} />
                          </button>
                        </div>
                      ) : null}

                      {/* Resizer Handle */}
                      {!isIndex && (
                        <div
                          role="separator"
                          aria-label={`Resize ${header.id}`}
                          className="resizer"
                          onMouseDown={header.getResizeHandler()}
                          onTouchStart={header.getResizeHandler()}
                        />
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {paddingTop > 0 && (
              <tr aria-hidden="true">
                <td
                  colSpan={totalColumns}
                  style={{ height: paddingTop, padding: 0, border: 0 }}
                />
              </tr>
            )}
            {virtualRows.map((virtual) => {
              const row = table.getRowModel().rows[virtual.index];
              if (!row) return null;
              const isSelected = selectedRowIndices.has(row.index);
              return (
                <tr
                  key={row.id}
                  className={`databricks-row ${isSelected ? "row-selected" : ""}`}
                >
                  {row.getVisibleCells().map((c) => (
                    <td
                      key={c.id}
                      style={
                        c.column.id === "__row_index__" ||
                        pinnedCols.has(c.column.id)
                          ? {
                              position: "sticky",
                              left:
                                c.column.id === "__row_index__"
                                  ? 0
                                  : (showRowNumbers ? 38 : 0) +
                                    orderedColumns
                                      .filter((name) => pinnedCols.has(name))
                                      .slice(
                                        0,
                                        orderedColumns
                                          .filter((name) =>
                                            pinnedCols.has(name),
                                          )
                                          .indexOf(c.column.id),
                                      )
                                      .reduce(
                                        (sum, name) =>
                                          sum +
                                          (table.getColumn(name)?.getSize() ??
                                            0),
                                        0,
                                      ),
                              zIndex: 2,
                              background: isSelected
                                ? "var(--violet-soft)"
                                : "var(--surface)",
                            }
                          : undefined
                      }
                      className={
                        c.column.id === "__row_index__"
                          ? "grid-index-cell"
                          : isSelected
                            ? "cell-in-selected-row"
                            : ""
                      }
                    >
                      {flexRender(c.column.columnDef.cell, c.getContext())}
                    </td>
                  ))}
                </tr>
              );
            })}
            {paddingBottom > 0 && (
              <tr aria-hidden="true">
                <td
                  colSpan={totalColumns}
                  style={{ height: paddingBottom, padding: 0, border: 0 }}
                />
              </tr>
            )}
          </tbody>
        </table>
        {filteredRows.length === 0 && (
          <p className="empty">
            {rows.length === 0
              ? "No rows to display."
              : "No matching rows found."}
          </p>
        )}
      </div>
    </div>
  );
}
