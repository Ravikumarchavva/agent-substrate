// Browser client for the workspace's same-origin /api/v1 proxy (session cookie, no tokens here).

export type Problem = {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  request_id?: string;
  errors?: { location: string; message: string }[];
};

export class ApiError extends Error {
  readonly code: string;
  constructor(
    readonly status: number,
    readonly problem: Problem,
  ) {
    const detail = problem.errors?.length
      ? `${problem.detail}: ${problem.errors.map((e) => `${e.location} ${e.message}`).join("; ")}`
      : problem.detail || `Request failed (${status})`;
    super(detail);
    this.code = (problem.type || "").split("/").pop() || "error";
  }
}

/** No attached compute is ready: the operator must start or attach it (never this application). */
export const isComputeUnavailable = (error: unknown) =>
  error instanceof ApiError && error.code === "compute-unavailable";

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, init);
  if (response.status === 401) {
    window.location.href = `/auth/login?next=${encodeURIComponent(window.location.pathname)}`;
  }
  if (!response.ok) {
    const problem = await response
      .json()
      .catch(() => ({ detail: response.statusText }));
    throw new ApiError(response.status, problem);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

export function post(value: unknown, method = "POST"): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  };
}
