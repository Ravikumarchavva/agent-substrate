import { threadApi } from "./threads";
import { messageApi } from "./messages";
import { taskApi } from "./tasks";
import { fileApi } from "./files";
import { chatApi } from "./chat";
import { adminApi } from "./admin";
import { audioApi } from "./audio";
import { scheduledApi } from "./scheduled";
import { workspaceApi } from "./workspace";
import { memoryApi } from "./memory";
import { artifactsApi } from "./artifacts";
import { branchApi } from "./branches";

export const api = {
  ...threadApi,
  ...messageApi,
  ...taskApi,
  ...fileApi,
  ...chatApi,
  ...adminApi,
  ...audioApi,
  ...scheduledApi,
  ...workspaceApi,
  ...memoryApi,
  ...artifactsApi,
  ...branchApi,
};
export { branchApi };

export type { ChatStreamRequest } from "./_client";
