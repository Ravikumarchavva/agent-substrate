"use client";

import { useQuery } from "@tanstack/react-query";
import { ExternalLink, LogOut, Settings, User } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Me } from "../lib/types";
import { api } from "./api";
import { ThemeSwitch } from "./theme";

export function UserMenu() {
  const [open, setOpen] = useState(false);
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => api<Me>("/me"),
    staleTime: 60_000,
  });
  const name = me.data?.name ?? "…";
  const initial = name.slice(0, 1).toUpperCase();
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }
    if (open) {
      document.addEventListener("mousedown", handleClickOutside);
      document.addEventListener("keydown", handleKeyDown);
      return () => {
        document.removeEventListener("mousedown", handleClickOutside);
        document.removeEventListener("keydown", handleKeyDown);
      };
    }
  }, [open]);

  return (
    <div className="user-menu-wrapper" ref={ref}>
      <button
        type="button"
        className="topbar-user-badge topbar-user-button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label="User account menu"
        title={`Signed in as ${me.data?.principal ?? ""}`}
      >
        <span>{initial}</span>
      </button>

      {open && (
        <div className="user-menu-dropdown panel" role="menu">
          <div className="user-menu-header">
            <div className="user-avatar-large">
              <span>{initial}</span>
            </div>
            <div className="user-info">
              <strong className="user-name">{name}</strong>
              <span className="user-email">
                {me.data?.email ?? me.data?.principal}
              </span>
              <span className="user-role-badge">
                {me.data?.is_admin
                  ? "Metastore admin"
                  : me.data?.groups.join(", ") || "Member"}
              </span>
            </div>
          </div>

          <div className="user-menu-divider" />

          <div className="user-menu-section">
            <div className="user-menu-section-label">APPEARANCE</div>
            <ThemeSwitch />
          </div>

          <div className="user-menu-divider" />

          <div className="user-menu-nav">
            <button
              type="button"
              className="user-menu-item"
              onClick={() => setOpen(false)}
            >
              <Settings size={14} />
              <span>Workspace Settings</span>
            </button>
            <a
              href="http://127.0.0.1:8020/api/v1/docs"
              target="_blank"
              rel="noreferrer"
              className="user-menu-item"
              onClick={() => setOpen(false)}
            >
              <ExternalLink size={14} />
              <span>API Documentation</span>
            </a>
          </div>

          <div className="user-menu-divider" />

          <div className="user-menu-footer">
            <a href="/auth/logout" className="user-menu-item sign-out">
              <LogOut size={14} />
              <span>Sign out</span>
            </a>
          </div>
        </div>
      )}
    </div>
  );
}
