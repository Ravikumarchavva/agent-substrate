import { ToolCall, UploadedFile } from "@/types";

// API service layer for backend communication
// All browser requests go through the Next.js rewrite proxy
// (/api/backend/* -> backend) to avoid CORS failures.
//
// This app is mounted at basePath: "/chat" (see next.config.ts) behind
// agent-substrate-platform's proxy. Next.js rewrites next/link hrefs and
// route-handler paths to include that prefix automatically, but a raw
// fetch("/api/backend/...") is just a literal browser request and is NOT
// basePath-aware — it has to be spelled out here or every call site would
// silently 404 in production.
// The one place the mount path is spelled. Every browser URL to this app's own route handlers or to the backend proxy builds on it.
export const APP_BASE = "/chat";
export const API_BASE = `${APP_BASE}/api/backend`;

/** URL of one of this app's own route handlers (`app/api/<path>`), e.g. `appApiUrl("/admin/users")`. */
export function appApiUrl(path: string): string {
  return `${APP_BASE}/api${path}`;
}

// Backend route is GET /files/{file_id}/download (agent-substrate
// routes/files.py) — file id alone is enough, no thread scoping needed.
export function buildFileContentUrl(fileId: string): string {
  return `${API_BASE}/files/${fileId}/download`;
}

// Resolves a `sandbox:<path>` markdown ref (a file the code interpreter saved
// in its working directory) to the workspace file-serve endpoint
// (agent-substrate routes/workspace.py::serve_file), scoped to the thread's
// session dir. Images render inline; other types download.
export function buildWorkspaceFileUrl(threadId: string, path: string): string {
  const clean = path.replace(/^sandbox:/, "").replace(/^\.?\//, "");
  return `${API_BASE}/workspace/file?thread_id=${encodeURIComponent(
    threadId,
  )}&path=${encodeURIComponent(clean)}`;
}

// Resolves an `object:<key>` reference — a tool-result image the backend
// resolved by reference rather than inlining as a base64 data URI (see
// agent-substrate agents/runtime/context/tool.py::_attachment_url and its
// module docstring for why the backend emits a bare scheme instead of a real
// path). Same shape as buildWorkspaceFileUrl's `sandbox:` resolution — the
// engine names the resource, this frontend's authenticated proxy serves it.
export function buildObjectUrl(ref: string): string {
  const key = ref.replace(/^object:/, "");
  return `${API_BASE}/files/object?key=${encodeURIComponent(key)}`;
}

export function withUploadedFileUrl(file: UploadedFile): UploadedFile {
  if (file.url?.startsWith("object:")) {
    return { ...file, url: buildObjectUrl(file.url) };
  }
  if (file.url) {
    return file;
  }
  return {
    ...file,
    url: buildFileContentUrl(file.id),
  };
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export function toUploadedFile(value: unknown): UploadedFile | null {
  if (!isRecord(value)) return null;
  if (typeof value.id !== "string" || typeof value.name !== "string") {
    return null;
  }

  return withUploadedFileUrl({
    id: value.id,
    thread_id: typeof value.thread_id === "string" ? value.thread_id : undefined,
    name: value.name,
    mime: typeof value.mime === "string" ? value.mime : "application/octet-stream",
    size: typeof value.size === "number" ? value.size : 0,
    url: typeof value.url === "string" ? value.url : undefined,
    // Lets AttachmentDocumentCard open a user-uploaded office file in the
    // read-only side-panel viewer — see UploadedFile's own doc comment.
    // This whitelist constructor is exactly why that stayed broken even
    // after the field was added everywhere else: the backend (Attachment
    // model), the wire payload, and the UploadedFile type all carried it
    // correctly, but this was the one place that actually builds the
    // object React renders, and it silently dropped any field not listed
    // here.
    session_path: typeof value.session_path === "string" ? value.session_path : undefined,
  });
}

export function getMessageAttachments(
  metadata: Record<string, unknown> | undefined,
): UploadedFile[] | undefined {
  const rawAttachments = metadata?.attachments;
  if (!Array.isArray(rawAttachments)) return undefined;

  const attachments = rawAttachments
    .map((attachment) => toUploadedFile(attachment))
    .filter((attachment): attachment is UploadedFile => attachment !== null);

  return attachments.length > 0 ? attachments : undefined;
}

export function isPersistentToolCall(toolCall: ToolCall): boolean {
  return Boolean(toolCall._meta?.ui?.httpUrl);
}

export type ChatStreamRequest = {
  thread_id: string;
  messages: Array<{ role: "user"; content: string }>;
  file_ids?: string[];
  system_instructions?: string;
  model?: string;
  branch_id?: string;
  /** How hard a reasoning model thinks; ignored by models that do not reason. */
  reasoning?: "off" | "low" | "medium" | "high";
};

function getStructuredErrorMessage(payload: unknown): string | null {
  if (!isRecord(payload)) {
    return null;
  }

  for (const key of ["error", "message", "detail", "details"]) {
    const value = payload[key];
    if (typeof value === "string" && value.trim()) {
      return value;
    }
  }

  return null;
}

/** What to tell a person about a failed request, by status. Never the response body: it may be a stack trace or an HTML error page. */
export function describeHttpStatus(status: number, fallback: string): string {
  if (status === 401) return "Your session has expired. Please sign in again.";
  if (status === 403) return "You don't have permission to do that.";
  if (status === 404) return "That wasn't found. It may have been moved or deleted.";
  if (status === 408 || status === 504) return "The request timed out. Please try again.";
  if (status === 413) return "That file is too large.";
  if (status === 429) return "You've reached a usage limit. It resets automatically; the usage meter shows when.";
  if (status >= 500) return "Something went wrong on our side. Please try again in a moment.";
  return fallback;
}

/** An HTTP failure with its status, so callers can react to 401/429 without parsing the message. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function getErrorMessage(res: Response, fallback: string): Promise<string> {
  const contentType = res.headers.get("content-type")?.toLowerCase() ?? "";
  const text = await res.text().catch(() => "");

  if (contentType.includes("application/json") || /^\s*[{[]/.test(text)) {
    try {
      const payload = JSON.parse(text) as unknown;
      return getStructuredErrorMessage(payload) ?? describeHttpStatus(res.status, fallback);
    } catch {
      return describeHttpStatus(res.status, fallback);
    }
  }

  // Plain text from our own backend is a short sentence; anything else (an HTML page, a long dump) is not for people.
  const looksLikeHtml = /^\s*<(!doctype|html|head|body)/i.test(text);
  if (text && !looksLikeHtml && text.length <= 300) return text;
  return describeHttpStatus(res.status, fallback);
}

export async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const isFormData = init?.body instanceof FormData;
  const headers = isFormData
    ? init?.headers
    : { "Content-Type": "application/json", ...init?.headers };
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    throw new ApiError(await getErrorMessage(res, `Request failed for ${path}`), res.status);
  }
  return res.json() as Promise<T>;
}

export async function requestVoid(path: string, init?: RequestInit): Promise<void> {
  const isFormData = init?.body instanceof FormData;
  const headers = isFormData
    ? init?.headers
    : { "Content-Type": "application/json", ...init?.headers };
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    throw new ApiError(await getErrorMessage(res, `Request failed for ${path}`), res.status);
  }
}

export async function requestJsonFromUrl<T>(url: string, init?: RequestInit): Promise<T> {
  const isFormData = init?.body instanceof FormData;
  const headers = isFormData
    ? init?.headers
    : { "Content-Type": "application/json", ...init?.headers };
  const res = await fetch(url, { ...init, headers });
  if (!res.ok) {
    throw new ApiError(await getErrorMessage(res, `Request failed for ${url}`), res.status);
  }
  return res.json() as Promise<T>;
}

export async function requestVoidFromUrl(url: string, init?: RequestInit): Promise<void> {
  const isFormData = init?.body instanceof FormData;
  const headers = isFormData
    ? init?.headers
    : { "Content-Type": "application/json", ...init?.headers };
  const res = await fetch(url, { ...init, headers });
  if (!res.ok) {
    throw new ApiError(await getErrorMessage(res, `Request failed for ${url}`), res.status);
  }
}
