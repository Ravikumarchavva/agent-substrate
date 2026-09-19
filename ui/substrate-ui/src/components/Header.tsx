"use client";

import { useState, useRef, useEffect } from "react";
import { GitBranch, Plus, Check, ChevronDown } from "lucide-react";
import { SidebarToggleIcon } from "@/components/SidebarToggleIcon";
import type { Branch } from "@/types";

interface HeaderProps {
  onOpenMobileSidebar?: () => void;
  desktopSidebarOpen?: boolean;
  threadName?: string;
  branches?: Branch[];
  activeBranchId?: string;
  onSelectBranch?: (branchId: string) => void;
  onOpenForkModal?: (sourceBranchId: string) => void;
}

export function Header({
  onOpenMobileSidebar,
  desktopSidebarOpen = true,
  threadName,
  branches = [],
  activeBranchId = "main",
  onSelectBranch,
  onOpenForkModal,
}: HeaderProps) {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setIsOpen(false);
      }
    }
    if (isOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [isOpen]);

  return (
    <header
      className={`flex h-12 items-center justify-between bg-background/80 backdrop-blur-md px-3 sm:px-4 border-b border-(--border)/50 z-20 shrink-0 ${
        !desktopSidebarOpen ? "pl-14 lg:pl-16" : ""
      }`}
      suppressHydrationWarning
    >
      {/* Left: mobile toggle + thread name + branch indicator */}
      <div className="flex items-center gap-2.5 min-w-0">
        {onOpenMobileSidebar && (
          <button
            onClick={onOpenMobileSidebar}
            className="shrink-0 rounded-xl p-1.5 hover:bg-(--card-hover) cursor-pointer lg:hidden"
            aria-label="Open sidebar"
          >
            <SidebarToggleIcon direction="open" className="h-4 w-4 text-(--muted)" />
          </button>
        )}

        <span className="text-[13px] font-medium truncate text-foreground/80">
          {threadName || "New Chat"}
        </span>

        {/* Branch Selector Pill */}
        {threadName && (
          <div className="relative ml-0.5" ref={dropdownRef}>
            <button
              type="button"
              onClick={() => setIsOpen((prev) => !prev)}
              className="flex items-center gap-1.5 rounded-lg border border-(--border) bg-(--card) px-2.5 py-1 text-xs text-foreground/90 hover:bg-(--card-hover) hover:border-(--border)/80 transition-all cursor-pointer shadow-xs"
              title="Switch or fork branch"
              aria-expanded={isOpen}
            >
              <GitBranch className="h-3.5 w-3.5 text-(--accent)" />
              <span className="font-mono text-[11px] font-semibold tracking-tight max-w-[140px] truncate">
                {activeBranchId}
              </span>
              <ChevronDown className="h-3 w-3 text-(--muted) opacity-70" />
            </button>

            {/* Dropdown Menu */}
            {isOpen && (
              <div className="absolute left-0 top-full mt-1.5 w-60 rounded-xl border border-(--border) bg-(--card) p-1.5 shadow-xl backdrop-blur-md z-50 animate-in fade-in zoom-in-95 duration-150">
                <div className="px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wider text-(--muted)">
                  Conversation Branches
                </div>
                <div className="max-h-52 overflow-y-auto space-y-0.5 py-1">
                  {(branches.length > 0 ? branches : [{ id: "main" } as Branch]).map((b) => (
                    <button
                      key={b.id}
                      type="button"
                      onClick={() => {
                        onSelectBranch?.(b.id);
                        setIsOpen(false);
                      }}
                      className={`flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-xs text-left cursor-pointer transition-colors ${
                        b.id === activeBranchId
                          ? "bg-(--accent)/10 text-(--accent) font-medium"
                          : "text-foreground/80 hover:bg-(--card-hover) hover:text-foreground"
                      }`}
                    >
                      <div className="flex items-center gap-2 truncate">
                        <GitBranch className="h-3.5 w-3.5 shrink-0 opacity-60" />
                        <span className="font-mono truncate">{b.id}</span>
                      </div>
                      {b.id === activeBranchId && (
                        <Check className="h-3.5 w-3.5 text-(--accent) shrink-0" />
                      )}
                    </button>
                  ))}
                </div>

                <div className="mt-1 pt-1 border-t border-(--border)/60">
                  <button
                    type="button"
                    onClick={() => {
                      setIsOpen(false);
                      onOpenForkModal?.(activeBranchId);
                    }}
                    className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs text-foreground/70 hover:bg-(--card-hover) hover:text-foreground cursor-pointer transition-colors"
                  >
                    <Plus className="h-3.5 w-3.5 text-(--accent)" />
                    <span>Fork from current branch...</span>
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </header>
  );
}