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
export const API_BASE = "/chat/api/backend";

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

export async function getErrorMessage(res: Response, fallback: string): Promise<string> {
  const contentType = res.headers.get("content-type")?.toLowerCase() ?? "";

  if (contentType.includes("application/json")) {
    const payload = await res.json().catch(() => null);
    return getStructuredErrorMessage(payload) ?? fallback;
  }

  const text = await res.text().catch(() => "");
  if (!text) {
    return fallback;
  }

  try {
    const payload = JSON.parse(text) as unknown;
    return getStructuredErrorMessage(payload) ?? text;
  } catch {
    return text;
  }
}

export async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const isFormData = init?.body instanceof FormData;
  const headers = isFormData
    ? init?.headers
    : { "Content-Type": "application/json", ...init?.headers };
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    throw new Error(await getErrorMessage(res, `Request failed for ${path}`));
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
    throw new Error(await getErrorMessage(res, `Request failed for ${path}`));
  }
}

export async function requestJsonFromUrl<T>(url: string, init?: RequestInit): Promise<T> {
  const isFormData = init?.body instanceof FormData;
  const headers = isFormData
    ? init?.headers
    : { "Content-Type": "application/json", ...init?.headers };
  const res = await fetch(url, { ...init, headers });
  if (!res.ok) {
    throw new Error(await getErrorMessage(res, `Request failed for ${url}`));
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
    throw new Error(await getErrorMessage(res, `Request failed for ${url}`));
  }
}
