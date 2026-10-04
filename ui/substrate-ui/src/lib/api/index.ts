import { threadApi } from "./threads";
import { approvalsApi } from "./approvals";
import { messageApi } from "./messages";
import { usageApi } from "./usage";
import { notificationsApi } from "./notifications";
import { preferencesApi } from "./preferences";
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
import { runsApi } from "./runs";
import { agentsApi } from "./agents";

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
  ...usageApi,
  ...notificationsApi,
  ...approvalsApi,
  ...preferencesApi,
  ...artifactsApi,
  ...branchApi,
  ...runsApi,
  ...agentsApi,
};
export { branchApi };

export type { ChatStreamRequest } from "./_client";
