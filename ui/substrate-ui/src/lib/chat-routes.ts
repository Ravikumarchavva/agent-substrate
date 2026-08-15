import type { SettingsTab } from "@/components/SettingsPanel";

/**
 * Type-guard: check if a value is a known settings tab.
 */
export function isSettingsTab(value: string | null): value is SettingsTab {
  return (
    value === "general" ||
    value === "apps" ||
    value === "llm" ||
    value === "search" ||
    value === "storage" ||
    value === "memory" ||
    value === "admin"
  );
}

/**
 * Parse the current pathname into structured chat route state.
 *
 * Supported patterns:
 *   /chat                → { threadId: null, settingsTab: null }
 *   /chat/<threadId>     → { threadId, settingsTab: null }
 *   /settings            → { threadId: null, settingsTab: "general" }
 *   /settings/<tab>      → { threadId: null, settingsTab: <tab> }
 */
export function parseChatPath(pathname: string): {
  threadId: string | null;
  settingsTab: SettingsTab | null;
} {
  const segments = pathname.split("/").filter(Boolean);

  // /settings or /settings/<tab>
  if (segments[0] === "settings") {
    const tab = segments[1] ?? null;
    return {
      threadId: null,
      settingsTab: isSettingsTab(tab) ? tab : "general",
    };
  }

  // /chat or /chat/<threadId>
  const chatSegments = segments[0] === "chat" ? segments.slice(1) : segments;

  if (chatSegments.length === 0) {
    return { threadId: null, settingsTab: null };
  }

  return { threadId: chatSegments[0] ?? null, settingsTab: null };
}

/**
 * Build a chat URL path (without settings).
 */
export function buildChatPath(threadId: string | null): string {
  if (threadId) return `/chat/${threadId}`;
  return "/chat";
}

/**
 * Build a settings URL path (top-level, independent of chat thread).
 */
export function buildSettingsPath(tab: SettingsTab = "general"): string {
  if (tab === "general") return "/settings";
  return `/settings/${tab}`;
}
