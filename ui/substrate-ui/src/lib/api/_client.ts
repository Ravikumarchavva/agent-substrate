import { ToolCall, UploadedFile } from "@/types";

// API service layer for backend communication
// All browser requests go through the Next.js rewrite proxy
// (/api/backend/* -> backend) to avoid CORS failures.
export const API_BASE = "/api/backend";

export function buildThreadFileContentUrl(threadId: string, fileId: string): string {
  return `${API_BASE}/threads/${threadId}/files/${fileId}/content`;
}

export function withUploadedFileUrl(file: UploadedFile): UploadedFile {
  if (file.url || !file.thread_id) {
    return file;
  }
  return {
    ...file,
    url: buildThreadFileContentUrl(file.thread_id, file.id),
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
