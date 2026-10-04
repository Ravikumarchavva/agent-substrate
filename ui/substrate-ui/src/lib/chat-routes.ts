import type { SettingsTab } from "@/components/SettingsPanel";

/**
 * Type-guard: check if a value is a known settings tab.
 */
export function isSettingsTab(value: string | null): value is SettingsTab {
  return (
    value === "general" ||
    value === "apps" ||
    value === "llm" ||
    value === "memory" ||
    value === "usage" ||
    value === "archived" ||
    value === "storage" ||
    value === "admin"
  );
}

/**
 * Parse the current pathname (from `usePathname()`, which is already
 * basePath-relative — see next.config.ts's `basePath: '/chat'`) into
 * structured chat route state.
 *
 * The chat route is the app's root-level optional catch-all
 * (`app/[[...slug]]/page.tsx`) — no literal "chat" segment of its own,
 * since basePath already supplies the app's one and only "/chat" mount
 * prefix externally. Adding a second literal "chat" segment here would
 * double it (a real bug this fixed — see git history).
 *
 * Supported patterns (basePath-relative):
 *   /                     → { threadId: null, settingsTab: null }
 *   /<threadId>           → { threadId, settingsTab: null }
 *   /settings             → { threadId: null, settingsTab: "general" }
 *   /settings/<tab>       → { threadId: null, settingsTab: <tab> }
 */
export type ChatView = "scheduled" | "approvals" | "notifications" | "agents";

const VIEWS: readonly string[] = ["scheduled", "approvals", "notifications", "agents"];

export function parseChatPath(pathname: string): {
  threadId: string | null;
  settingsTab: SettingsTab | null;
  /** A page of its own in place of the conversation (`/scheduled`, `/approvals`). */
  view: ChatView | null;
} {
  const segments = pathname.split("/").filter(Boolean);

  // /scheduled or /approvals
  if (segments[0] && VIEWS.includes(segments[0])) {
    return { threadId: null, settingsTab: null, view: segments[0] as ChatView };
  }

  // /settings or /settings/<tab>
  if (segments[0] === "settings") {
    const tab = segments[1] ?? null;
    const resolvedTab = isSettingsTab(tab) ? tab : "general";
    return {
      threadId: null,
      settingsTab: resolvedTab,
      view: null,
    };
  }

  // / or /<threadId>
  return { threadId: segments[0] ?? null, settingsTab: null, view: null };
}

/**
 * Build the REAL browser path for a chat thread, including basePath.
 *
 * Required by the two call sites (page.tsx's selectThread/promoteThreadUrl)
 * that write it via the raw `window.history.pushState/replaceState` API to
 * avoid remounting the page on thread switch (see those functions'
 * comments); that API is not basePath-aware the way `router.push`/
 * `router.replace` are, so it needs the literal "/chat" prefix spelled out.
 * Matches the same hardcoded-"/chat"-literal convention already used for
 * the same reason in `lib/api/_client.ts`'s `API_BASE`.
 *
 * Do NOT pass this to `router.push`/`router.replace` — Next.js already
 * prepends basePath for those, so this would double it to "/chat/chat".
 * Use `buildChatRoute` instead for router navigation.
 */
export function buildChatPath(threadId: string | null): string {
  if (threadId) return `/chat/${threadId}`;
  return "/chat";
}

/**
 * Build a basePath-RELATIVE chat route, for `router.push`/`router.replace`
 * (which auto-prepend basePath themselves — see buildChatPath's doc for why
 * these are two different functions, not one).
 */
export function buildChatRoute(threadId: string | null): string {
  if (threadId) return `/${threadId}`;
  return "/";
}

/** The browser path of a page like Scheduled (basePath included, for the raw History API). */
export function buildViewPath(view: ChatView): string {
  return `/chat/${view}`;
}

/**
 * Build a settings URL path (top-level, independent of chat thread).
 * Already basePath-relative — correct for `router.push`/`router.replace`.
 */
export function buildSettingsPath(tab: SettingsTab = "general"): string {
  if (tab === "general") return "/settings";
  return `/settings/${tab}`;
}
