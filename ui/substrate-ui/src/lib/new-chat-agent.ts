/** The agent the next new conversation will be with. Kept in sessionStorage because opening the Agents page and coming back remounts the chat page, which would lose component state. */
const KEY = "substrate-new-chat-agent";

export function getNewChatAgent(): string {
  try {
    return sessionStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

export function setNewChatAgent(agentId: string): void {
  try {
    if (agentId) sessionStorage.setItem(KEY, agentId);
    else sessionStorage.removeItem(KEY);
  } catch {
    // Private mode or blocked storage: the choice just doesn't survive a remount.
  }
}
