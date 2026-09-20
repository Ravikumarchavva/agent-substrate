"use client";

import { useState, useRef, useEffect } from "react";
import { GitBranch, Check, ChevronDown, Pencil } from "lucide-react";
import { SidebarToggleIcon } from "@/components/SidebarToggleIcon";
import type { Branch } from "@/types";

interface HeaderProps {
  onOpenMobileSidebar?: () => void;
  desktopSidebarOpen?: boolean;
  threadName?: string;
  branches?: Branch[];
  activeBranchId?: string;
  onSelectBranch?: (branchId: string) => void;
  onRenameBranch?: (branchId: string, newName: string) => void;
}

export function Header({
  onOpenMobileSidebar,
  desktopSidebarOpen = true,
  threadName,
  branches = [],
  activeBranchId = "main",
  onSelectBranch,
  onRenameBranch,
}: HeaderProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [editingBranchId, setEditingBranchId] = useState<string | null>(null);
  const [editNameValue, setEditNameValue] = useState("");
  const dropdownRef = useRef<HTMLDivElement>(null);
  const editInputRef = useRef<HTMLInputElement>(null);

  const activeBranch = branches.find((b) => b.id === activeBranchId);
  const activeDisplayName = activeBranch?.name || activeBranchId;

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsOpen(false);
        setEditingBranchId(null);
      }
    }
    if (isOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [isOpen]);

  useEffect(() => {
    if (editingBranchId && editInputRef.current) {
      editInputRef.current.focus();
      editInputRef.current.select();
    }
  }, [editingBranchId]);

  const handleStartRename = (e: React.MouseEvent, b: Branch) => {
    e.stopPropagation();
    setEditingBranchId(b.id);
    setEditNameValue(b.name || b.id);
  };

  const handleSaveRename = (branchId: string) => {
    const trimmed = editNameValue.trim();
    if (trimmed && onRenameBranch) {
      onRenameBranch(branchId, trimmed);
    }
    setEditingBranchId(null);
  };

  const handleCancelRename = (e?: React.MouseEvent) => {
    e?.stopPropagation();
    setEditingBranchId(null);
  };

  return (
    <header
      className={`flex h-12 items-center justify-between bg-background/80 backdrop-blur-md px-3 sm:px-4 border-b border-(--border)/40 z-20 shrink-0 ${
        !desktopSidebarOpen ? "pl-14 lg:pl-16" : ""
      }`}
      suppressHydrationWarning
    >
      {/* Left: mobile toggle + modern branch breadcrumb */}
      <div className="flex items-center gap-2 min-w-0">
        {onOpenMobileSidebar && (
          <button
            onClick={onOpenMobileSidebar}
            className="shrink-0 rounded-lg p-1.5 hover:bg-(--card-hover) text-(--muted) hover:text-foreground cursor-pointer lg:hidden transition-colors"
            aria-label="Open sidebar"
          >
            <SidebarToggleIcon direction="open" className="h-4 w-4" />
          </button>
        )}

        {/* Branch Selector Pill */}
        {(threadName || branches.length > 0) && (
          <div className="relative" ref={dropdownRef}>
            <button
              type="button"
              onClick={() => setIsOpen((prev) => !prev)}
              className={`group flex items-center gap-1.5 h-8 px-2.5 rounded-lg text-xs font-medium cursor-pointer transition-all select-none ${
                isOpen
                  ? "bg-(--card-hover) text-foreground border border-(--border)/80 shadow-xs"
                  : "text-foreground/80 hover:text-foreground hover:bg-(--card-hover) border border-(--border)/40 hover:border-(--border)/70"
              }`}
              title="Switch branch"
              aria-expanded={isOpen}
            >
              <GitBranch className="h-3.5 w-3.5 text-(--muted) group-hover:text-foreground transition-colors shrink-0" />
              <span className="tracking-tight max-w-[180px] truncate text-[12px] font-medium text-foreground">
                {activeDisplayName}
              </span>
              <ChevronDown
                className={`h-3 w-3 text-(--muted) opacity-70 transition-transform duration-200 shrink-0 ${
                  isOpen ? "rotate-180 text-foreground opacity-100" : ""
                }`}
              />
            </button>

            {/* Dropdown Menu */}
            {isOpen && (
              <div className="absolute left-0 top-full mt-2 w-72 rounded-2xl border border-(--border)/80 bg-(--card)/95 p-1.5 shadow-2xl backdrop-blur-2xl z-50 animate-in fade-in-0 zoom-in-95 duration-150 ring-1 ring-black/5 dark:ring-white/5">
                {/* Header with Title */}
                <div className="flex items-center justify-between px-2.5 py-1.5 border-b border-(--border)/40 mb-1">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[11px] font-semibold tracking-wider uppercase text-(--muted)">
                      Branches
                    </span>
                    <span className="px-1.5 py-0.2 rounded-md text-[10px] font-mono font-medium bg-(--border)/30 text-(--muted)">
                      {branches.length || 1}
                    </span>
                  </div>
                </div>

                {/* Branches List */}
                <div className="max-h-60 overflow-y-auto space-y-0.5 py-0.5 scrollbar-thin">
                  {(branches.length > 0 ? branches : [{ id: "main" } as Branch]).map((b) => {
                    const isEditing = editingBranchId === b.id;
                    const displayName = b.name || b.id;
                    const isSelected = b.id === activeBranchId;

                    if (isEditing) {
                      return (
                        <div
                          key={b.id}
                          className="p-2 rounded-xl bg-(--card-hover) border border-(--border)/80 shadow-xs space-y-2"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <input
                            ref={editInputRef}
                            type="text"
                            value={editNameValue}
                            onChange={(e) => setEditNameValue(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter") {
                                e.preventDefault();
                                handleSaveRename(b.id);
                              } else if (e.key === "Escape") {
                                e.preventDefault();
                                handleCancelRename();
                              }
                            }}
                            className="w-full rounded-lg bg-background border border-(--border) focus:border-foreground/30 focus:ring-2 focus:ring-foreground/10 px-2.5 py-1.5 text-xs text-foreground placeholder:text-(--muted) outline-none font-medium transition-all"
                            placeholder="Branch display name..."
                          />
                          <div className="flex items-center justify-between pt-0.5">
                            <span className="text-[10px] text-(--muted) font-mono">
                              ↵ save · esc cancel
                            </span>
                            <div className="flex items-center gap-1.5">
                              <button
                                type="button"
                                onClick={handleCancelRename}
                                className="inline-flex h-6 items-center justify-center px-2 rounded-md text-[11px] text-(--muted) hover:text-foreground hover:bg-background transition-colors cursor-pointer"
                              >
                                Cancel
                              </button>
                              <button
                                type="button"
                                onClick={() => handleSaveRename(b.id)}
                                className="inline-flex h-6 items-center justify-center px-2.5 rounded-md text-[11px] font-semibold bg-foreground text-background hover:opacity-90 transition-opacity cursor-pointer shadow-xs"
                              >
                                Save
                              </button>
                            </div>
                          </div>
                        </div>
                      );
                    }

                    return (
                      <div
                        key={b.id}
                        className={`group relative flex items-center justify-between rounded-xl px-2.5 py-2 text-xs cursor-pointer transition-all ${
                          isSelected
                            ? "bg-(--card-hover) text-foreground font-medium"
                            : "text-foreground/80 hover:bg-(--card-hover)/60 hover:text-foreground"
                        }`}
                        onClick={() => {
                          onSelectBranch?.(b.id);
                          setIsOpen(false);
                        }}
                      >
                        <div className="flex items-center gap-2.5 min-w-0 flex-1">
                          <div
                            className={`w-6 h-6 rounded-lg flex items-center justify-center shrink-0 transition-colors ${
                              isSelected
                                ? "bg-foreground/10 text-foreground"
                                : "bg-(--border)/20 text-(--muted) group-hover:text-foreground"
                            }`}
                          >
                            <GitBranch className="h-3.5 w-3.5" />
                          </div>
                          <div className="flex flex-col min-w-0">
                            <span className="truncate text-xs font-medium text-foreground tracking-tight">
                              {displayName}
                            </span>
                            <div className="flex items-center gap-1.5 mt-0.5">
                              {b.id === "main" && (
                                <span className="text-[9px] font-mono px-1 py-0.2 rounded bg-(--border)/30 text-(--muted)">
                                  default
                                </span>
                              )}
                              {b.name && b.name !== b.id && (
                                <span className="font-mono text-[9px] text-(--muted) truncate">
                                  {b.id}
                                </span>
                              )}
                            </div>
                          </div>
                        </div>

                        <div className="flex items-center gap-1 shrink-0 ml-2">
                          <button
                            type="button"
                            onClick={(e) => handleStartRename(e, b)}
                            className="flex h-6 w-6 items-center justify-center rounded-md hover:bg-background text-(--muted) hover:text-foreground transition-all opacity-0 group-hover:opacity-100 cursor-pointer"
                            title="Rename branch"
                          >
                            <Pencil className="h-3.5 w-3.5 shrink-0" />
                          </button>
                          {isSelected && (
                            <div className="flex h-6 w-6 items-center justify-center text-foreground shrink-0">
                              <Check className="h-3.5 w-3.5 stroke-[2.5] shrink-0" />
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </header>
  );
}