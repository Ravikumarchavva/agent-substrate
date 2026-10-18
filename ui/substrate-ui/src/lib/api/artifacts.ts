import { requestJson, requestVoid } from "./_client";
import type { Artifact, ArtifactScope } from "@/types";

/**
 * Artifacts — curated OKF concepts (memory, notes, promoted files) stored at
 * two scopes. `session` needs a threadId; `global` spans conversations.
 *
 * Scope is a query parameter on every endpoint rather than a separate route
 * tree, mirroring the backend (routes/artifacts.py).
 */
function scopeQuery(scope: ArtifactScope, threadId?: string): string {
  const params = new URLSearchParams({ scope });
  if (scope === "session" && threadId) params.set("thread_id", threadId);
  return params.toString();
}

export const artifactsApi = {
  async listArtifacts(
    scope: ArtifactScope,
    opts?: { threadId?: string; type?: string; tags?: string[]; includeDeprecated?: boolean },
  ): Promise<Artifact[]> {
    const params = new URLSearchParams({ scope });
    if (scope === "session" && opts?.threadId) params.set("thread_id", opts.threadId);
    if (opts?.type) params.set("type", opts.type);
    // Repeated `tag` params — the backend treats them conjunctively.
    for (const tag of opts?.tags ?? []) params.append("tag", tag);
    if (opts?.includeDeprecated) params.set("include_deprecated", "true");
    return requestJson<Artifact[]>(`/artifacts?${params.toString()}`);
  },

  async createArtifact(
    scope: ArtifactScope,
    payload: {
      type?: string;
      title?: string;
      description?: string;
      body?: string;
      tags?: string[];
    },
    threadId?: string,
  ): Promise<Artifact> {
    return requestJson<Artifact>(`/artifacts?${scopeQuery(scope, threadId)}`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  async updateArtifact(
    slug: string,
    scope: ArtifactScope,
    payload: {
      title?: string;
      description?: string;
      body?: string;
      tags?: string[];
      status?: string;
    },
    threadId?: string,
  ): Promise<Artifact> {
    return requestJson<Artifact>(
      `/artifacts/${encodeURIComponent(slug)}?${scopeQuery(scope, threadId)}`,
      { method: "PATCH", body: JSON.stringify(payload) },
    );
  },

  /** Session → global. There is no reverse direction. */
  async promoteArtifact(slug: string, threadId: string): Promise<Artifact> {
    const params = new URLSearchParams({ thread_id: threadId });
    return requestJson<Artifact>(
      `/artifacts/${encodeURIComponent(slug)}/promote?${params.toString()}`,
      { method: "POST" },
    );
  },

  /**
   * Soft by default — marks the artifact deprecated, keeping its history.
   * `hard` erases the document outright.
   */
  async deleteArtifact(
    slug: string,
    scope: ArtifactScope,
    opts?: { threadId?: string; hard?: boolean },
  ): Promise<void> {
    const params = new URLSearchParams({ scope });
    if (scope === "session" && opts?.threadId) params.set("thread_id", opts.threadId);
    if (opts?.hard) params.set("hard", "true");
    await requestVoid(`/artifacts/${encodeURIComponent(slug)}?${params.toString()}`, {
      method: "DELETE",
    });
  },
};
