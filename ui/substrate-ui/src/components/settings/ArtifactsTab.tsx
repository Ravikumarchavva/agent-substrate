"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Archive,
  ArrowUpCircle,
  BadgeCheck,
  Bot,
  Loader2,
  RefreshCw,
  Tag,
  Trash2,
  User,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Artifact, ArtifactScope, ArtifactTrust } from "@/types";

/** Trust tier comes from OKF's `verified` list — who vouched for this, if anyone. */
const TRUST_META: Record<ArtifactTrust, { label: string; Icon: typeof Bot; className: string }> = {
  "Human-reviewed": {
    label: "You",
    Icon: BadgeCheck,
    className: "text-emerald-400",
  },
  "Machine-confirmed": {
    label: "Assistant",
    Icon: Bot,
    className: "text-(--muted)",
  },
  Unverified: {
    label: "Unverified",
    Icon: User,
    className: "text-amber-400",
  },
};

export function ArtifactsTab({ threadId }: { threadId?: string | null }) {
  const [scope, setScope] = useState<ArtifactScope>("global");
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busySlug, setBusySlug] = useState<string | null>(null);
  const [activeTag, setActiveTag] = useState<string | null>(null);
  const [showDeprecated, setShowDeprecated] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setArtifacts(
        await api.listArtifacts(scope, {
          threadId: threadId ?? undefined,
          includeDeprecated: showDeprecated,
        }),
      );
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Artifacts aren't available for this deployment.",
      );
      setArtifacts([]);
    } finally {
      setLoading(false);
    }
  }, [scope, threadId, showDeprecated]);

  useEffect(() => {
    void load();
  }, [load]);

  // Reset a tag filter that no longer exists in the current scope, otherwise
  // switching scopes can leave the list mysteriously empty.
  useEffect(() => {
    setActiveTag(null);
  }, [scope]);

  const allTags = useMemo(
    () => Array.from(new Set(artifacts.flatMap((a) => a.tags))).sort(),
    [artifacts],
  );

  const visible = useMemo(
    () => (activeTag ? artifacts.filter((a) => a.tags.includes(activeTag)) : artifacts),
    [artifacts, activeTag],
  );

  const handleDelete = async (artifact: Artifact) => {
    if (
      !confirm(
        `Remove "${artifact.title || artifact.slug}"? It's marked deprecated and kept for history, not erased.`,
      )
    )
      return;
    setBusySlug(artifact.slug);
    try {
      await api.deleteArtifact(artifact.slug, scope, { threadId: threadId ?? undefined });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to remove artifact.");
    } finally {
      setBusySlug(null);
    }
  };

  const handlePromote = async (artifact: Artifact) => {
    if (!threadId) return;
    setBusySlug(artifact.slug);
    try {
      await api.promoteArtifact(artifact.slug, threadId);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to promote artifact.");
    } finally {
      setBusySlug(null);
    }
  };

  const sessionUnavailable = scope === "session" && !threadId;

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-2">
          <h2 className="text-3xl font-semibold tracking-tight text-foreground">Artifacts</h2>
          <p className="max-w-3xl text-sm leading-6 text-(--muted)">
            Things worth keeping — what the assistant has learned about you, plus
            notes and files it has set aside. <strong>Global</strong> artifacts apply to
            every conversation; <strong>session</strong> artifacts stay in the one they came
            from until you promote them.
          </p>
        </div>
        <button
          onClick={() => void load()}
          disabled={loading}
          className="inline-flex cursor-pointer items-center gap-2 self-start rounded-xl bg-(--card) px-4 py-2 text-sm text-(--muted) transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
          style={{ boxShadow: "var(--shadow-sm)" }}
          aria-label="Refresh artifacts"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {/* Scope switch */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="inline-flex rounded-xl bg-(--card) p-1" style={{ boxShadow: "var(--shadow-sm)" }}>
          {(["global", "session"] as ArtifactScope[]).map((s) => (
            <button
              key={s}
              onClick={() => setScope(s)}
              className={`cursor-pointer rounded-lg px-3 py-1.5 text-sm capitalize transition-colors ${
                scope === s
                  ? "bg-background text-foreground"
                  : "text-(--muted) hover:text-foreground"
              }`}
            >
              {s}
            </button>
          ))}
        </div>
        <label className="ml-auto inline-flex cursor-pointer items-center gap-2 text-xs text-(--muted)">
          <input
            type="checkbox"
            checked={showDeprecated}
            onChange={(e) => setShowDeprecated(e.target.checked)}
            className="cursor-pointer accent-(--accent)"
          />
          Show retired
        </label>
      </div>

      {allTags.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <Tag className="h-3.5 w-3.5 text-(--muted)" />
          {allTags.map((tag) => (
            <button
              key={tag}
              onClick={() => setActiveTag(activeTag === tag ? null : tag)}
              className={`cursor-pointer rounded-full px-2.5 py-1 text-[11px] transition-colors ${
                activeTag === tag
                  ? "bg-(--accent)/15 text-foreground ring-1 ring-(--accent)/40"
                  : "bg-(--badge-bg) text-(--muted) hover:text-foreground"
              }`}
            >
              {tag}
            </button>
          ))}
        </div>
      )}

      {error && (
        <p className="rounded-[18px] bg-(--badge-bg) px-4 py-3 text-sm text-(--muted)">{error}</p>
      )}

      {sessionUnavailable ? (
        <div className="rounded-xl border border-(--border) bg-(--card) px-6 py-10 text-center text-sm text-(--muted)">
          Open a conversation to see its session artifacts.
        </div>
      ) : loading ? (
        <div className="flex items-center justify-center py-10">
          <Loader2 className="h-5 w-5 animate-spin text-(--muted)" />
        </div>
      ) : visible.length === 0 ? (
        <div className="flex flex-col items-center rounded-xl border border-(--border) bg-(--card) px-6 py-10 text-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-full bg-(--card-hover) text-(--muted)">
            <Archive className="h-6 w-6" />
          </div>
          <h4 className="mt-4 text-lg font-semibold text-foreground">Nothing here yet</h4>
          <p className="mt-1 max-w-sm text-sm text-(--muted)">
            {activeTag
              ? "No artifacts with that tag."
              : scope === "global"
                ? "When the assistant learns something worth keeping across conversations, it shows up here."
                : "Notes the assistant sets aside in this conversation appear here."}
          </p>
        </div>
      ) : (
        <div
          className="space-y-2 rounded-[24px] p-3"
          style={{ background: "var(--card)", boxShadow: "var(--shadow-sm)" }}
        >
          {visible.map((artifact) => {
            const trust = TRUST_META[artifact.trust] ?? TRUST_META.Unverified;
            const retired = artifact.status === "deprecated";
            return (
              <div
                key={artifact.slug}
                className={`flex items-start gap-3 rounded-[18px] bg-background px-4 py-3 ${
                  retired ? "opacity-55" : ""
                }`}
              >
                <div className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-(--badge-bg) text-(--muted)">
                  <Archive className="h-4 w-4" />
                </div>

                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium text-foreground">
                      {artifact.title || artifact.slug}
                    </span>
                    <span className="rounded bg-(--badge-bg) px-1.5 py-0.5 text-[10px] text-(--muted)">
                      {artifact.type}
                    </span>
                    {retired && (
                      <span className="rounded bg-(--badge-bg) px-1.5 py-0.5 text-[10px] text-amber-400">
                        retired
                      </span>
                    )}
                    <span
                      className={`inline-flex items-center gap-1 text-[10px] ${trust.className}`}
                      title={`Trust: ${artifact.trust}`}
                    >
                      <trust.Icon className="h-3 w-3" />
                      {trust.label}
                    </span>
                  </div>

                  {artifact.body && (
                    <p className="mt-1 whitespace-pre-wrap text-sm text-(--muted)">
                      {artifact.body}
                    </p>
                  )}

                  {artifact.tags.length > 0 && (
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {artifact.tags.map((tag) => (
                        <span
                          key={tag}
                          className="rounded-full bg-(--badge-bg) px-2 py-0.5 text-[10px] text-(--muted)"
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                </div>

                <div className="flex shrink-0 items-center gap-1">
                  {scope === "session" && !retired && threadId && (
                    <button
                      onClick={() => void handlePromote(artifact)}
                      disabled={busySlug === artifact.slug}
                      className="cursor-pointer rounded-lg p-2 text-(--muted) transition-colors hover:bg-(--card-hover) hover:text-foreground disabled:opacity-40"
                      title="Keep across all conversations"
                      aria-label="Promote artifact to global"
                    >
                      {busySlug === artifact.slug ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <ArrowUpCircle className="h-4 w-4" />
                      )}
                    </button>
                  )}
                  {!retired && (
                    <button
                      onClick={() => void handleDelete(artifact)}
                      disabled={busySlug === artifact.slug}
                      className="cursor-pointer rounded-lg p-2 text-(--muted) transition-colors hover:bg-rose-500/10 hover:text-rose-400 disabled:opacity-40"
                      aria-label="Remove artifact"
                    >
                      {busySlug === artifact.slug ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Trash2 className="h-4 w-4" />
                      )}
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
