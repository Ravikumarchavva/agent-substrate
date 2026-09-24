/** Curated knowledge stored as OKF concepts. See agent-substrate's
 *  integrations/artifacts/ — this mirrors ArtifactOut in routes/artifacts.py. */

/** `session` is one conversation; `global` spans all of them. */
export type ArtifactScope = "session" | "global";

/** Derived from OKF's `verified` list, not stored directly. */
export type ArtifactTrust = "Unverified" | "Machine-confirmed" | "Human-reviewed";

export interface Artifact {
  slug: string;
  scope: ArtifactScope;
  /** OKF's only required field — free-form, e.g. "Memory", "Decision". */
  type: string;
  title?: string | null;
  description?: string | null;
  body: string;
  tags: string[];
  /** draft | stable | deprecated — deprecated is retained, not deleted. */
  status: string;
  /** Set when the concept describes an underlying file rather than text. */
  resource?: string | null;
  trust: ArtifactTrust;
  generated?: { by?: string; at?: string } | null;
  stale_after?: string | null;
}
