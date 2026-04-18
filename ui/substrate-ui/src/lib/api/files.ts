import { UploadedFile } from "@/types";
import { requestJson, requestVoid, withUploadedFileUrl } from "./_client";

export const fileApi = {
  async uploadFile(threadId: string, file: File): Promise<UploadedFile> {
    const form = new FormData();
    form.append("file", file);
    const uploaded = await requestJson<UploadedFile>(`/threads/${threadId}/files`, {
      method: "POST",
      body: form,
    });
    return withUploadedFileUrl(uploaded);
  },

  async listFiles(threadId: string): Promise<UploadedFile[]> {
    const files = await requestJson<UploadedFile[]>(`/threads/${threadId}/files`);
    return files.map((file) => withUploadedFileUrl(file));
  },

  async deleteFile(threadId: string, fileId: string): Promise<void> {
    await requestVoid(`/threads/${threadId}/files/${fileId}`, {
      method: "DELETE",
    });
  },
};
