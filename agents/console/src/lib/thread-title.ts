/** A new conversation is titled from its first message. The one with an agent is named after the agent, and a first message never retitles it. */
export function shouldAutoTitle(thread: { agent_id?: string | null } | null | undefined, messageCount: number): boolean {
  return messageCount === 0 && !thread?.agent_id;
}
