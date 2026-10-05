import { Info } from "lucide-react";
import { Button } from "@/design";
import { Avatar } from "@/components/groups/Avatar";
import type { Agent } from "@/lib/api/agents";

/** What a conversation with an agent opens on before anything has been said: who it is, what it is for, and where to change that. */
export function AgentIntro({ agent, onOpenInfo }: { agent: Agent | null; onOpenInfo: () => void }) {
  if (!agent) return <div className="h-full" aria-busy />;
  return (
    <div className="flex h-full items-center justify-center px-6">
      <div className="flex max-w-sm flex-col items-center gap-3 text-center">
        <Avatar name={agent.name} src={agent.avatar} className="size-24 text-4xl" />
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">{agent.name}</h1>
        <p className="text-sm text-muted">{agent.role || "No role set yet."}</p>
        <p className="text-xs text-muted">This is your one conversation with {agent.name}. It remembers what you share, and keeps its files between chats.</p>
        <Button variant="ghost" size="sm" onClick={onOpenInfo}>
          <Info /> About {agent.name}
        </Button>
      </div>
    </div>
  );
}
