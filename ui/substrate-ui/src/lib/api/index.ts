import { threadApi } from "./threads";
import { messageApi } from "./messages";
import { taskApi } from "./tasks";
import { fileApi } from "./files";
import { chatApi } from "./chat";
import { adminApi } from "./admin";
import { audioApi } from "./audio";

export const api = {
  ...threadApi,
  ...messageApi,
  ...taskApi,
  ...fileApi,
  ...chatApi,
  ...adminApi,
  ...audioApi,
};

export type { ChatStreamRequest } from "./_client";
