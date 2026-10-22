"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, FileText, Globe, Loader2, Monitor, RefreshCw, Search, X } from "lucide-react";
import { Button, Segmented, cn } from "@/design";
import { api } from "@/lib/api";
import { browserSteps, stepsOf, terminalSteps, type Step } from "@/lib/computer";
import { formatFileSize } from "@/lib/file-utils";
import { reportError } from "@/lib/report-error";
import type { Message, WorkspaceFile } from "@/types";

type Tab = "files" | "browser" | "activity" | "terminal";

const POLL_MS = 4000;

/**
 * The agent's computer: the files in its workspace, what it has done (every tool call, live), and what its code printed. For an agent the workspace is its own and
 * persists between conversations; for a plain chat it is that conversation's. Opening a file shows it in the side viewer.
 */
export function ComputerPanel({ workspaceId, title, messages, running, viewerOpen = false, onOpenFile, onClose }: {
  workspaceId: string | null;
  /** The agent's name, or "Computer" for a plain conversation. */
  title: string;
  messages: Message[];
  running: boolean;
  /** A file viewer is open beside the chat: on screens too narrow for both, this panel steps aside until it is closed. */
  viewerOpen?: boolean;
  onOpenFile: (workspaceId: string, relativePath: string, name: string) => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<Tab>("files");
  const [files, setFiles] = useState<WorkspaceFile[] | null>(null);

  const load = useCallback(async () => {
    if (!workspaceId) return;
    try {
      const all = await api.listWorkspaceFiles();
      setFiles(all.filter((f) => f.session_id === workspaceId).sort((a, b) => b.modified_at - a.modified_at));
    } catch (err) {
      setFiles((current) => current ?? []);
      reportError("Couldn't list the files", err);
    }
  }, [workspaceId]);

  // Refresh when it opens, when a run ends (it may have written files) and, while one is running, every few seconds.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- load on open / when the run settles
    void load();
    if (!running) return;
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [load, running]);

  const steps = useMemo(() => stepsOf(messages, running), [messages, running]);
  const terminal = useMemo(() => terminalSteps(steps), [steps]);
  const browser = useMemo(() => browserSteps(steps), [steps]);

  return (
    <aside className={cn("hidden h-full w-96 shrink-0 flex-col border-l border-border bg-background", viewerOpen ? "2xl:flex" : "lg:flex")} aria-label="Computer">
      <div className="flex items-center gap-2 px-4 py-3">
        <Monitor className="size-4 text-muted" aria-hidden />
        <h2 className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">{title}</h2>
        {running && (
          <span className="flex items-center gap-1 text-xs text-muted">
            <Loader2 className="size-3 animate-spin" aria-hidden /> Working
          </span>
        )}
        <Button variant="ghost" size="icon" aria-label="Close" onClick={onClose}>
          <X />
        </Button>
      </div>
      <div className="px-4 pb-3">
        <Segmented<Tab>
          label="Computer view"
          value={tab}
          onChange={setTab}
          options={[
            { value: "files", label: `Files${files?.length ? ` ${files.length}` : ""}` },
            { value: "browser", label: `Browser${browser.length ? ` ${browser.length}` : ""}` },
            { value: "activity", label: `Activity${steps.length ? ` ${steps.length}` : ""}` },
            { value: "terminal", label: "Terminal" },
          ]}
        />
      </div>
      <div className="scroll-area min-h-0 flex-1 px-4 pb-4">
        {tab === "files" && <Files files={files} workspaceId={workspaceId} onRefresh={() => void load()} onOpen={onOpenFile} />}
        {tab === "browser" && <Browser steps={browser} />}
        {tab === "activity" && <Activity steps={steps} />}
        {tab === "terminal" && <Terminal steps={terminal} />}
      </div>
    </aside>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return <p className="py-10 text-center text-xs leading-relaxed text-muted">{children}</p>;
}

function Files({ files, workspaceId, onRefresh, onOpen }: { files: WorkspaceFile[] | null; workspaceId: string | null; onRefresh: () => void; onOpen: (workspaceId: string, path: string, name: string) => void }) {
  if (!workspaceId) return <Empty>Files appear here once the conversation starts.</Empty>;
  if (files === null) return <Empty>Loading…</Empty>;
  if (files.length === 0) return <Empty>No files yet. Files the assistant makes, and ones you attach, show up here.</Empty>;
  return (
    <>
      <div className="mb-1 flex justify-end">
        <Button variant="ghost" size="sm" onClick={onRefresh}>
          <RefreshCw /> Refresh
        </Button>
      </div>
      <ul className="divide-y divide-border rounded-lg border border-border">
        {files.map((f) => (
          <li key={f.path}>
            <Button variant="ghost" onClick={() => onOpen(workspaceId, f.path.replace(/^.*\/workspace\/shared\//, ""), f.name)} className="h-auto w-full justify-start gap-2.5 rounded-none px-3 py-2 text-left font-normal">
              <FileText className="size-4 shrink-0 text-muted" aria-hidden />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm text-foreground">{f.name}</span>
                <span className="block text-xs text-muted">
                  {formatFileSize(f.size_bytes)} · {f.owner === "agent" ? "made by the assistant" : "yours"}
                </span>
              </span>
            </Button>
          </li>
        ))}
      </ul>
    </>
  );
}

/** The pages the assistant opened and the searches it ran, newest first. Opening one goes to the real page; this is a history, not a live view. */
function Browser({ steps }: { steps: Step[] }) {
  if (steps.length === 0) return <Empty>No web pages yet. Pages the assistant opens and searches it runs are listed here.</Empty>;
  return (
    <ul className="space-y-1">
      {[...steps].reverse().map((s) => {
        const web = s.web!;
        const Icon = web.kind === "page" ? Globe : Search;
        return (
          <li key={s.id} className="flex items-start gap-2.5 rounded-lg px-2 py-1.5">
            <Icon className="mt-0.5 size-3.5 shrink-0 text-muted" aria-hidden />
            <span className="min-w-0 flex-1">
              {web.kind === "page" ? (
                <a href={web.target} target="_blank" rel="noopener noreferrer" className="block truncate text-sm text-foreground underline-offset-2 hover:underline">
                  {web.target}
                </a>
              ) : (
                <span className="block truncate text-sm text-foreground">Searched for “{web.target}”</span>
              )}
              <span className="text-xs text-muted">{s.state === "running" ? "Loading…" : s.state === "failed" ? "Failed" : web.kind === "page" ? "Opened" : "Searched"}</span>
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function StateIcon({ state }: { state: Step["state"] }) {
  if (state === "running") return <Loader2 className="size-3.5 animate-spin text-muted" aria-label="running" />;
  if (state === "failed") return <X className="size-3.5 text-danger" aria-label="failed" />;
  return <Check className="size-3.5 text-success" aria-label="done" />;
}

function Activity({ steps }: { steps: Step[] }) {
  if (steps.length === 0) return <Empty>Nothing yet. Each tool the assistant uses is listed here as it happens.</Empty>;
  return (
    <ol className="space-y-1">
      {[...steps].reverse().map((s) => (
        <li key={s.id} className="flex items-start gap-2.5 rounded-lg px-2 py-1.5">
          <span className="mt-0.5">
            <StateIcon state={s.state} />
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-1.5 text-sm font-medium text-foreground">
              {s.name}
              {s.risk && s.risk !== "safe" && <span className="rounded bg-badge px-1.5 text-xs text-muted">{s.risk}</span>}
            </span>
            {s.summary && <span className="block truncate font-mono text-xs text-muted">{s.summary}</span>}
          </span>
        </li>
      ))}
    </ol>
  );
}

function Terminal({ steps }: { steps: Step[] }) {
  if (steps.length === 0) return <Empty>No code has run yet. Commands and their output show here.</Empty>;
  return (
    <div className="space-y-3">
      {steps.map((s) => (
        <div key={s.id} className="overflow-hidden rounded-lg border border-border bg-card">
          <pre className="overflow-x-auto border-b border-border px-3 py-2 font-mono text-xs text-foreground">
            <span className="select-none text-muted">$ </span>
            {s.code || s.summary}
          </pre>
          <pre className={cn("max-h-64 overflow-auto whitespace-pre-wrap px-3 py-2 font-mono text-xs", s.state === "failed" ? "text-danger" : "text-muted")}>
            {s.state === "running" ? "Running…" : s.output || "(no output)"}
          </pre>
        </div>
      ))}
    </div>
  );
}
