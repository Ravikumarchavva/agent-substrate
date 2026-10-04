"use client";

import { useEffect, useState } from "react";
import { Bot, CalendarClock, FolderOpen, ShieldQuestion, type LucideIcon } from "lucide-react";
import { Button } from "@/design";
import { SubstrateMark } from "@/components/SubstrateMark";
import type { Agent } from "@/lib/api/agents";

function greetingFor(hour: number): string {
  return hour < 5 ? "Working late" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
}

/** The icon with the title beside it and what it does underneath; the same height as its neighbours, sized to its text. */
function Card({ icon: Icon, title, hint, onClick }: { icon: LucideIcon; title: string; hint: string; onClick: () => void }) {
  return (
    <Button
      variant="ghost"
      onClick={onClick}
      className="h-auto! min-h-0 w-full flex-col items-start justify-start gap-2.5 whitespace-normal rounded-xl border border-border bg-card p-3.5 text-left font-normal hover:bg-card-hover sm:p-4"
    >
      <span className="flex items-center gap-2.5">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-badge text-muted">
          <Icon className="size-4" aria-hidden />
        </span>
        <span className="text-sm font-medium text-foreground">{title}</span>
      </span>
      <span className="block text-xs leading-relaxed text-muted">{hint}</span>
    </Button>
  );
}

/**
 * What a new conversation opens on: a greeting and four things particular to this product (work on a schedule, agents with their own files, approvals,
 * and the files themselves).
 * No generic starters ("research a topic", "analyse data"): any assistant does those, so they say nothing about this one.
 */
export function HomeScreen({ name, agents, onOpenScheduled, onOpenAgents, onOpenApprovals, onOpenFiles }: {
  name?: string | null;
  agents: Agent[];
  onOpenScheduled: () => void;
  onOpenAgents: () => void;
  onOpenApprovals: () => void;
  onOpenFiles: () => void;
}) {
  // Decided after mount: the hour differs between the server and the browser.
  const [greeting, setGreeting] = useState("Welcome");
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the hour is only known in the browser
    setGreeting(greetingFor(new Date().getHours()));
  }, []);
  const first = name?.trim().split(/\s+/)[0];

  return (
    <div className="flex h-full items-center justify-center">
      <div className="w-full max-w-chat space-y-6 px-3 py-10 sm:px-6">
        <header className="substrate-fade-up flex items-center gap-4">
          <SubstrateMark className="size-10 shrink-0 text-foreground" />
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-foreground">{first ? `${greeting}, ${first}` : greeting}</h1>
            <p className="mt-0.5 text-sm text-muted">What would you like to get done?</p>
          </div>
        </header>

        <div className="grid gap-2.5 sm:grid-cols-4">
          <Card icon={CalendarClock} title="Schedule work" hint="A morning briefing or a weekly review, delivered to you." onClick={onOpenScheduled} />
          <Card
            icon={Bot}
            title={agents.length ? "Your agents" : "Make an agent"}
            hint={agents.length ? agents.slice(0, 3).map((a) => a.name).join(" · ") : "A saved role with its own instructions, tools and files."}
            onClick={onOpenAgents}
          />
          <Card icon={ShieldQuestion} title="Stay in control" hint="It asks before it changes anything, and you approve each step." onClick={onOpenApprovals} />
          <Card icon={FolderOpen} title="Your files" hint="What you attach and what it makes, kept in one place." onClick={onOpenFiles} />
        </div>
      </div>
    </div>
  );
}
