import { UploadedFile } from "@/types";
import { requestJson, requestVoid, withUploadedFileUrl } from "./_client";

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
};
