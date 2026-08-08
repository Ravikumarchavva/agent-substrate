import { UploadedFile } from "@/types";
import { API_BASE, requestJson, requestVoid, withUploadedFileUrl } from "./_client";

export interface FileStatus {
  staged_at: string | null;
  staging_error: string | null;
  page_count: number | null;
  created_at: string;
}

export const fileApi = {
  // Backend route is POST /files/upload (thread_id rides as a form field,
  // not a path segment) — see agent-substrate routes/files.py.
  async uploadFile(threadId: string, file: File): Promise<UploadedFile> {
    const form = new FormData();
    form.append("file", file);
    form.append("thread_id", threadId);
    const uploaded = await requestJson<UploadedFile>(`/files/upload`, {
      method: "POST",
      body: form,
    });
    return withUploadedFileUrl(uploaded);
  },

  async deleteFile(_threadId: string, fileId: string): Promise<void> {
    await requestVoid(`/files/${fileId}`, {
      method: "DELETE",
    });
  },

  // Lightweight polling target for the composer's per-attachment progress
  // ring — see routes/files.py::get_file_status. Returns null on any
  // failure so a transient polling error doesn't crash the composer; the
  // caller just tries again on the next interval tick.
  async getFileStatus(fileId: string): Promise<FileStatus | null> {
    try {
      const res = await fetch(`${API_BASE}/files/${fileId}/status`, {
        credentials: "include",
      });
      if (!res.ok) return null;
      return (await res.json()) as FileStatus;
    } catch {
      return null;
    }
  },
};
