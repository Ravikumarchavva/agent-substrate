import { WorkspaceUsage, WorkspaceFile } from "@/types";
import { requestJson, requestVoid } from "./_client";

export const workspaceApi = {
  async getWorkspaceUsage(): Promise<WorkspaceUsage> {
    return requestJson<WorkspaceUsage>("/workspace/usage");
  },

  async listWorkspaceFiles(): Promise<WorkspaceFile[]> {
    const { files } = await requestJson<{ files: WorkspaceFile[] }>("/workspace/files");
    return files;
  },

  async deleteWorkspaceFile(path: string): Promise<void> {
    await requestVoid(`/workspace/files?path=${encodeURIComponent(path)}`, {
      method: "DELETE",
    });
  },
};
