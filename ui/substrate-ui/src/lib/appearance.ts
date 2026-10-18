/** Appearance choices kept per browser (and synced to the account by `preferences-sync`): how wide the chat is, and whether to animate. */
export const CHAT_WIDTH_KEY = "chat_width";
export const MOTION_KEY = "motion";

export type ChatWidth = "narrow" | "medium" | "wide";
export type Motion = "system" | "reduced";

export const CHAT_WIDTHS: Record<ChatWidth, string> = { narrow: "44rem", medium: "54rem", wide: "68rem" };

export function readChatWidth(): ChatWidth {
  if (typeof window === "undefined") return "medium";
  const v = localStorage.getItem(CHAT_WIDTH_KEY);
  return v === "narrow" || v === "wide" ? v : "medium";
}

export function readMotion(): Motion {
  if (typeof window === "undefined") return "system";
  return localStorage.getItem(MOTION_KEY) === "reduced" ? "reduced" : "system";
}

/** Put the stored choices on the page. Called at start and after every change (and after the account's choices are pulled in). */
export function applyAppearance(): void {
  const root = document.documentElement;
  root.style.setProperty("--chat-width", CHAT_WIDTHS[readChatWidth()]);
  root.classList.toggle("reduce-motion", readMotion() === "reduced");
}

export function setChatWidth(value: ChatWidth): void {
  if (value === "medium") localStorage.removeItem(CHAT_WIDTH_KEY);
  else localStorage.setItem(CHAT_WIDTH_KEY, value);
  applyAppearance();
}

export function setMotion(value: Motion): void {
  if (value === "system") localStorage.removeItem(MOTION_KEY);
  else localStorage.setItem(MOTION_KEY, value);
  applyAppearance();
}
