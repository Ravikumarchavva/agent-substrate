/** The agent the Agents page should open on (Edit agent in a chat sets it). Kept in sessionStorage because opening the page remounts the chat page, which would lose component state. */
const KEY = "substrate-agent-to-edit";

export function setAgentToEdit(agentId: string): void {
  try {
    sessionStorage.setItem(KEY, agentId);
  } catch {
    // Blocked storage: the page opens on its list instead.
  }
}

/** The agent to open on, once: reading it clears it. */
export function takeAgentToEdit(): string | null {
  try {
    const id = sessionStorage.getItem(KEY);
    sessionStorage.removeItem(KEY);
    return id;
  } catch {
    return null;
  }
}
