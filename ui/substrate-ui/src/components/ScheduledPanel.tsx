"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  Bell,
  CalendarClock,
  ChevronDown,
  Clock,
  FileText,
  GraduationCap,
  Loader2,
  MessageSquare,
  MoreHorizontal,
  Pause,
  Pencil,
  Play,
  RefreshCw,
  Search,
  Send,
  Sparkles,
  Trash2,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "@/lib/api";
import type { ScheduledTask, ScheduledTaskRun } from "@/types";
import {
  Badge,
  Button,
  Checkbox,
  Dialog,
  DialogContent,
  DialogFooter,
  Input,
  Menu,
  MenuContent,
  MenuItem,
  MenuSeparator,
  MenuTrigger,
  Page,
  PageEmpty,
  PageHeading,
  Pane,
  SettingGroup,
  SettingRow,
  Section,
  Select,
  Textarea,
  cn,
  confirmAction,
  toast,
} from "@/design";
import { reportError } from "@/lib/report-error";
import { DEFAULT_CHOICE, FREQUENCIES, WEEKDAYS, describe, toChoice, toSchedule, type Choice, type Frequency } from "@/lib/schedule";

interface ScheduledPanelProps {
  onBack: () => void;
  /** Open the conversation a task (or one of its runs) belongs to. */
  onOpenThread?: (threadId: string) => void;
}

type TaskType = ScheduledTask["task_type"];

interface Draft {
  name: string;
  prompt: string;
  choice: Choice;
  task_type: TaskType;
  ask_before_acting: boolean;
  email_results: boolean;
  auto_disable: boolean;
  lookback_runs: number;
}

const BLANK: Draft = { name: "", prompt: "", choice: DEFAULT_CHOICE, task_type: "report", ask_before_acting: true, email_results: false, auto_disable: false, lookback_runs: 5 };

/** Starting points shown on an empty page; picking one opens the form filled in. */
const TEMPLATES: { icon: typeof FileText; name: string; prompt: string; task_type: TaskType; choice: Partial<Choice> }[] = [
  { icon: FileText, name: "Daily briefing", prompt: "Summarise what needs my attention today: the news in my field, anything I asked you to keep an eye on, and what I should do first.", task_type: "report", choice: { frequency: "weekdays", time: "08:00" } },
  { icon: Activity, name: "Monitor a topic", prompt: "Watch for news or mentions of [topic] and tell me only when something new and important appears.", task_type: "monitor", choice: { frequency: "daily", time: "09:00" } },
  { icon: GraduationCap, name: "Weekly review", prompt: "Write a Friday summary of what we worked on this week and what is still open.", task_type: "learning", choice: { frequency: "weekly", weekday: 5, time: "16:00" } },
  { icon: Bell, name: "Reminder", prompt: "Remind me to [thing to do].", task_type: "reminder", choice: { frequency: "daily", time: "09:00" } },
  { icon: Sparkles, name: "Content ideas", prompt: "Draft a few post ideas based on the latest news in my industry.", task_type: "report", choice: { frequency: "weekly", weekday: 1, time: "09:00" } },
];

const ICONS: Record<TaskType, typeof FileText> = { report: FileText, monitor: Activity, reminder: Bell, learning: GraduationCap };

const SORTS = [
  { value: "next", label: "Sort by next run" },
  { value: "name", label: "Sort by name" },
  { value: "recent", label: "Sort by last run" },
];

const PERMISSIONS = [
  { value: "ask", label: "Ask me before it changes anything" },
  { value: "auto", label: "Act on its own (destructive actions still ask)" },
];

const scheduleOf = (t: ScheduledTask) => ({ kind: t.kind, expression: t.cron_expression });
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—");

function StatusBadge({ status }: { status: ScheduledTask["status"] }) {
  const tone = status === "active" ? "success" : status === "paused" ? "warning" : status === "error" ? "danger" : "neutral";
  return <Badge tone={tone}>{status === "active" ? "Active" : status === "paused" ? "Paused" : status === "error" ? "Error" : "Done"}</Badge>;
}

export function ScheduledPanel({ onOpenThread }: ScheduledPanelProps) {
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<ScheduledTask | null>(null);
  const [sort, setSort] = useState("next");
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null); // the create dialog, when open
  const [describeOpen, setDescribeOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      setTasks(await api.getScheduledTasks());
    } catch (err) {
      reportError("Couldn't load your scheduled tasks", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const replace = (updated: ScheduledTask) => {
    setTasks((all) => all.map((t) => (t.id === updated.id ? updated : t)));
    setSelected((s) => (s?.id === updated.id ? updated : s));
  };

  const runNow = async (task: ScheduledTask) => {
    try {
      await api.runScheduledTaskNow(task.id);
      toast.success("Started", "The result will appear under its runs.");
      setTimeout(() => void load(), 1500);
    } catch (err) {
      reportError("Couldn't start the run", err);
    }
  };

  const toggle = async (task: ScheduledTask) => {
    const status = task.status === "active" ? "paused" : "active";
    try {
      replace(await api.updateScheduledTask(task.id, { status }));
    } catch (err) {
      reportError(`Couldn't ${status === "paused" ? "pause" : "resume"} the task`, err);
    }
  };

  const remove = async (task: ScheduledTask) => {
    if (!(await confirmAction({ title: "Delete this task?", description: "Its run history goes with it. This can't be undone.", confirmLabel: "Delete", danger: true }))) return;
    try {
      await api.deleteScheduledTask(task.id);
      setTasks((all) => all.filter((t) => t.id !== task.id));
      setSelected(null);
    } catch (err) {
      reportError("Couldn't delete the task", err);
    }
  };

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = tasks.filter((t) => !q || t.name.toLowerCase().includes(q) || t.prompt.toLowerCase().includes(q));
    const at = (iso: string | null, missing: number) => (iso ? new Date(iso).getTime() : missing);
    return [...list].sort((a, b) =>
      sort === "name" ? a.name.localeCompare(b.name) : sort === "recent" ? at(b.last_run_at, 0) - at(a.last_run_at, 0) : at(a.next_run_at, Number.MAX_SAFE_INTEGER) - at(b.next_run_at, Number.MAX_SAFE_INTEGER),
    );
  }, [tasks, query, sort]);

  const newTaskMenu = (
    <Menu>
      <MenuTrigger asChild>
        <Button variant="primary">
          New task <ChevronDown />
        </Button>
      </MenuTrigger>
      <MenuContent align="end">
        <MenuItem onSelect={() => setDescribeOpen(true)}>
          <Sparkles /> Describe it
        </MenuItem>
        <MenuItem onSelect={() => setDraft(BLANK)}>
          <Pencil /> Set up manually
        </MenuItem>
      </MenuContent>
    </Menu>
  );

  const dialogs = (
    <>
      <CreateDialog
        draft={draft}
        onClose={() => setDraft(null)}
        onCreated={(task) => {
          setDraft(null);
          setTasks((all) => [task, ...all]);
          setSelected(task);
        }}
      />
      <DescribeDialog open={describeOpen} onClose={() => setDescribeOpen(false)} onParsed={(d) => { setDescribeOpen(false); setDraft(d); }} />
    </>
  );

  // With tasks, the page is split: the list stays on the left and the task you open fills the right, so there is no going back and forth. On a narrow
  // screen it is one or the other, with a back arrow.
  if (!loading && tasks.length > 0) {
    return (
      <div className="@container h-full w-full bg-background text-foreground">
        <div className="grid h-full min-h-0 @4xl:grid-cols-[minmax(22rem,26rem)_minmax(0,1fr)]">
          <div className={cn("min-h-0 @4xl:border-r @4xl:border-border", selected && "hidden @4xl:block")}>
            <Pane>
              <PageHeading title="Scheduled tasks" subtitle="Run tasks on a schedule or whenever you need them." actions={newTaskMenu} />
              <div className="flex items-center gap-2">
                <div className="relative min-w-0 flex-1">
                  <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted" aria-hidden />
                  <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search" aria-label="Search tasks" className="pl-8" />
                </div>
                <Select value={sort} onValueChange={setSort} options={SORTS} className="w-44 shrink-0" aria-label="Sort tasks" />
              </div>
              {visible.length === 0 ? (
                <p className="py-10 text-center text-sm text-muted">No task matches “{query}”.</p>
              ) : (
                <ul className="space-y-1.5">
                  {visible.map((task) => {
                    const Icon = ICONS[task.task_type] ?? CalendarClock;
                    const open = selected?.id === task.id;
                    return (
                      <li key={task.id} className={cn("flex items-center gap-1 rounded-xl border px-2 py-2 transition-colors", open ? "border-accent/40 bg-accent/12" : "border-transparent hover:bg-card-hover")}>
                        <button type="button" onClick={() => setSelected(task)} aria-current={open} className="flex min-w-0 flex-1 cursor-pointer items-center gap-3 px-1 text-left">
                          <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-badge text-muted">
                            <Icon className="size-4" aria-hidden />
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm font-medium text-foreground">{task.name}</span>
                            <span className="block truncate text-xs text-muted">
                              {describe(scheduleOf(task))}
                              {task.status === "active" && task.next_run_at ? ` · next ${when(task.next_run_at)}` : ""}
                            </span>
                          </span>
                          {task.status !== "active" && <StatusBadge status={task.status} />}
                        </button>
                        <Button variant="ghost" size="icon" aria-label={`Run ${task.name} now`} onClick={() => void runNow(task)}>
                          <Play />
                        </Button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </Pane>
          </div>
          <div className={cn("min-h-0", !selected && "hidden @4xl:block")}>
            {selected ? (
              <TaskDetail key={selected.id} task={selected} onBack={() => setSelected(null)} onChange={replace} onRun={runNow} onToggle={toggle} onDelete={remove} onOpenThread={onOpenThread} />
            ) : (
              <Pane className="items-center justify-center text-center">
                <Clock className="size-8 text-muted" aria-hidden />
                <p className="text-sm font-medium text-foreground">Pick a task to see its runs and settings</p>
              </Pane>
            )}
          </div>
        </div>
        {dialogs}
      </div>
    );
  }

  return (
    <Page title="Scheduled tasks" subtitle="Run tasks on a schedule or whenever you need them." actions={newTaskMenu}>
      {loading ? (
        <div className="flex flex-1 items-center justify-center text-muted">
          <Loader2 className="size-5 animate-spin" aria-label="Loading" />
        </div>
      ) : (
        <>
          <PageEmpty icon={Clock} title="No scheduled tasks yet">
            Have the assistant do something on a schedule, like a morning briefing, and read the result when it is ready.
          </PageEmpty>
          <Section title="Start from an example">
            <ul className="grid gap-2 sm:grid-cols-2">
              {TEMPLATES.map((t) => (
                <li key={t.name}>
                  <button
                    type="button"
                    onClick={() => setDraft({ ...BLANK, name: t.name, prompt: t.prompt, task_type: t.task_type, choice: { ...DEFAULT_CHOICE, ...t.choice } })}
                    className="flex w-full cursor-pointer items-start gap-3 rounded-lg p-2 text-left transition-colors hover:bg-card-hover"
                  >
                    <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-badge text-muted">
                      <t.icon className="size-4" aria-hidden />
                    </span>
                    <span className="min-w-0">
                      <span className="block text-sm font-medium text-foreground">{t.name}</span>
                      <span className="line-clamp-2 text-xs text-muted">{t.prompt}</span>
                      <span className="mt-1 flex items-center gap-1 text-xs text-muted">
                        <Clock className="size-3" aria-hidden /> {describe(toSchedule({ ...DEFAULT_CHOICE, ...t.choice }))}
                      </span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </Section>
        </>
      )}
      {dialogs}
    </Page>
  );
}

/** The schedule controls shared by the create form and a task's settings: how often, and at what time. */
function ScheduleFields({ choice, onChange }: { choice: Choice; onChange: (c: Choice) => void }) {
  const timed = choice.frequency !== "hourly" && choice.frequency !== "custom";
  return (
    <>
      <Select value={choice.frequency} onValueChange={(v) => onChange({ ...choice, frequency: v as Frequency })} options={FREQUENCIES} className="w-40" aria-label="Frequency" />
      {choice.frequency === "weekly" && (
        <Select value={String(choice.weekday)} onValueChange={(v) => onChange({ ...choice, weekday: Number(v) })} options={WEEKDAYS.map((d, i) => ({ value: String(i), label: d }))} className="w-36" aria-label="Day of the week" />
      )}
      {choice.frequency === "monthly" && (
        <Input type="number" min={1} max={28} value={choice.day} onChange={(e) => onChange({ ...choice, day: Math.min(28, Math.max(1, Number(e.target.value) || 1)) })} className="w-20" aria-label="Day of the month" />
      )}
      {timed && <Input type="time" value={choice.time} onChange={(e) => onChange({ ...choice, time: e.target.value || "08:00" })} className="w-32" aria-label="Time" />}
      {choice.frequency === "custom" && <Input value={choice.custom} onChange={(e) => onChange({ ...choice, custom: e.target.value })} placeholder="*/15 * * * *" className="w-44 font-mono" aria-label="Cron expression" />}
    </>
  );
}

function CreateDialog({ draft, onClose, onCreated }: { draft: Draft | null; onClose: () => void; onCreated: (t: ScheduledTask) => void }) {
  const [form, setForm] = useState<Draft>(BLANK);
  const [busy, setBusy] = useState(false);
  const [more, setMore] = useState(false);
  useEffect(() => {
    if (draft) setForm(draft);
  }, [draft]);

  const schedule = toSchedule(form.choice);
  const ready = form.name.trim() && form.prompt.trim() && schedule.expression;

  const create = async () => {
    setBusy(true);
    try {
      onCreated(
        await api.createScheduledTask({
          name: form.name.trim(),
          prompt: form.prompt.trim(),
          cron_expression: schedule.expression,
          kind: schedule.kind,
          task_type: form.task_type,
          lookback_runs: form.lookback_runs,
          auto_disable: form.auto_disable,
          email_results: form.email_results,
          ask_before_acting: form.ask_before_acting,
        }),
      );
    } catch (err) {
      reportError("Couldn't create the task", err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={draft !== null} onOpenChange={(o) => !o && onClose()}>
      <DialogContent title="New scheduled task" className="max-w-xl">
        <div className="space-y-4">
          <label className="block space-y-1">
            <span className="text-xs font-medium text-muted">Name</span>
            <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Daily briefing" />
          </label>
          <label className="block space-y-1">
            <span className="text-xs font-medium text-muted">Instructions</span>
            <Textarea rows={5} value={form.prompt} onChange={(e) => setForm({ ...form, prompt: e.target.value })} placeholder="Plan my meals for the week and write the shopping list." />
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <span className="w-24 text-sm text-foreground">Frequency</span>
            <ScheduleFields choice={form.choice} onChange={(choice) => setForm({ ...form, choice })} />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="w-24 text-sm text-foreground">Permissions</span>
            <Select value={form.ask_before_acting ? "ask" : "auto"} onValueChange={(v) => setForm({ ...form, ask_before_acting: v === "ask" })} options={PERMISSIONS} className="w-80 max-w-full" aria-label="Permissions" />
          </div>
          <div className="rounded-lg border border-border">
            <button type="button" onClick={() => setMore(!more)} aria-expanded={more} className="flex w-full cursor-pointer items-center justify-between px-3 py-2 text-sm text-foreground">
              Advanced settings <ChevronDown className={`size-4 text-muted transition-transform ${more ? "rotate-180" : ""}`} aria-hidden />
            </button>
            {more && (
              <div className="space-y-3 border-t border-border p-3">
                <Checkbox checked={form.email_results} onChange={(e) => setForm({ ...form, email_results: e.target.checked })} label="Email me the result of each run" />
                <Checkbox checked={form.auto_disable} onChange={(e) => setForm({ ...form, auto_disable: e.target.checked })} label="Stop after the first run that has something to report" />
                <label className="flex items-center gap-2 text-sm text-foreground">
                  Remember the last
                  <Input type="number" min={0} max={20} value={form.lookback_runs} onChange={(e) => setForm({ ...form, lookback_runs: Math.max(0, Number(e.target.value) || 0) })} className="w-16" aria-label="Runs to remember" />
                  runs
                </label>
              </div>
            )}
          </div>
          <p className="text-xs text-muted">{describe(schedule)}</p>
        </div>
        <DialogFooter>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!ready || busy} onClick={() => void create()}>
            {busy ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function DescribeDialog({ open, onClose, onParsed }: { open: boolean; onClose: () => void; onParsed: (d: Draft) => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    try {
      const p = await api.parseScheduledTaskText(text);
      onParsed({ ...BLANK, name: p.name, prompt: p.prompt, task_type: p.task_type, choice: toChoice({ kind: p.kind, expression: p.cron_expression }), auto_disable: p.task_type === "monitor" });
      setText("");
    } catch (err) {
      reportError("Couldn't work out that schedule", err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent title="Describe the task" description="Say what you want and when, like “every weekday at 8 summarise AI news”. You can adjust it before saving.">
        <Textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} placeholder="Every Friday at 4pm, summarise what we worked on this week." autoFocus />
        <DialogFooter>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!text.trim() || busy} onClick={() => void go()}>
            {busy ? "Working it out…" : "Continue"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function TaskDetail({ task, onBack, onChange, onRun, onToggle, onDelete, onOpenThread }: {
  task: ScheduledTask;
  onBack: () => void;
  onChange: (t: ScheduledTask) => void;
  onRun: (t: ScheduledTask) => Promise<void>;
  onToggle: (t: ScheduledTask) => Promise<void>;
  onDelete: (t: ScheduledTask) => Promise<void>;
  onOpenThread?: (threadId: string) => void;
}) {
  const [runs, setRuns] = useState<ScheduledTaskRun[] | null>(null);
  const [editing, setEditing] = useState(false);
  const [prompt, setPrompt] = useState(task.prompt);
  const [choice, setChoice] = useState<Choice>(toChoice(scheduleOf(task)));
  const [feedback, setFeedback] = useState("");

  const loadRuns = useCallback(async () => {
    try {
      setRuns(await api.getScheduledTaskRuns(task.id, { limit: 50, include_silent: true }));
    } catch (err) {
      setRuns([]);
      reportError("Couldn't load this task's runs", err);
    }
  }, [task.id]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- load this task's runs when it opens
    void loadRuns();
  }, [loadRuns]);

  const save = async (body: Parameters<typeof api.updateScheduledTask>[1], saved?: string) => {
    try {
      onChange(await api.updateScheduledTask(task.id, body));
      if (saved) toast.success(saved);
      return true;
    } catch (err) {
      reportError("Couldn't save that", err);
      return false;
    }
  };

  const changeSchedule = async (next: Choice) => {
    setChoice(next);
    const s = toSchedule(next);
    if (s.expression) await save({ cron_expression: s.expression, kind: s.kind });
  };

  const sendFeedback = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!feedback.trim()) return;
    try {
      await api.addScheduledTaskFeedback(task.id, feedback.trim());
      setFeedback("");
      toast.success("Noted", "It will use this the next time the task runs.");
    } catch (err) {
      reportError("Couldn't save that", err);
    }
  };

  return (
    <Pane>
      <PageHeading
        title={task.name}
      subtitle={`${describe(scheduleOf(task))}${task.status === "active" && task.next_run_at ? ` · next run ${when(task.next_run_at)}` : ""}`}
        onBack={onBack}
        backClassName="@4xl:hidden"
        actions={
        <>
          <Button variant="primary" onClick={() => void onRun(task).then(() => setTimeout(() => void loadRuns(), 2000))}>
            <Play /> Run now
          </Button>
          {onOpenThread && (
            <Button onClick={() => onOpenThread(task.thread_id)}>
              <MessageSquare /> Open conversation
            </Button>
          )}
          <Menu>
            <MenuTrigger asChild>
              <Button variant="ghost" size="icon" aria-label="More">
                <MoreHorizontal />
              </Button>
            </MenuTrigger>
            <MenuContent align="end">
              <MenuItem onSelect={() => void onToggle(task)}>
                {task.status === "active" ? <Pause /> : <Play />} {task.status === "active" ? "Pause" : "Resume"}
              </MenuItem>
              <MenuSeparator />
              <MenuItem tone="danger" onSelect={() => void onDelete(task)}>
                <Trash2 /> Delete
              </MenuItem>
            </MenuContent>
          </Menu>
        </>
      }
      />
      <>
      <Section title="Instructions">
        {editing ? (
          <div className="space-y-2">
            <Textarea rows={5} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
            <div className="flex justify-end gap-2">
              <Button onClick={() => { setPrompt(task.prompt); setEditing(false); }}>Cancel</Button>
              <Button variant="primary" disabled={!prompt.trim()} onClick={() => void save({ prompt: prompt.trim() }, "Saved").then((ok) => ok && setEditing(false))}>
                Save
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex items-start justify-between gap-3">
            <p className="select-text whitespace-pre-wrap text-sm leading-relaxed text-foreground">{task.prompt}</p>
            <Button variant="ghost" size="icon" aria-label="Edit instructions" onClick={() => setEditing(true)}>
              <Pencil />
            </Button>
          </div>
        )}
      </Section>

      <SettingGroup title="Settings">
        <SettingRow label="Frequency" description="When it runs.">
          <ScheduleFields choice={choice} onChange={(c) => void changeSchedule(c)} />
        </SettingRow>
        <SettingRow label="Permissions" description="What it may do without asking you first.">
          <Select value={task.ask_before_acting === false ? "auto" : "ask"} onValueChange={(v) => void save({ ask_before_acting: v === "ask" }, v === "ask" ? "It will ask before changing anything" : "It will act on its own")} options={PERMISSIONS} className="w-80 max-w-full" aria-label="Permissions" />
        </SettingRow>
        <SettingRow label="Email the result" description="Send each run's result to your account email.">
          <Checkbox checked={!!task.email_results} onChange={(e) => void save({ email_results: e.target.checked })} label="On" />
        </SettingRow>
        <SettingRow label="Stop after one report" description="Finish the task the first time a run has something to say.">
          <Checkbox checked={task.auto_disable} onChange={(e) => void save({ auto_disable: e.target.checked })} label="On" />
        </SettingRow>
      </SettingGroup>

      <Section title="Runs">
        {runs === null ? (
          <div className="flex justify-center py-6 text-muted">
            <Loader2 className="size-5 animate-spin" aria-label="Loading" />
          </div>
        ) : runs.length === 0 ? (
          <p className="py-4 text-center text-sm text-muted">No runs yet. Use “Run now” to try it.</p>
        ) : (
          <ul className="divide-y divide-border">
            {runs.map((run) => (
              <RunRow key={run.id} run={run} onRetry={() => void onRun(task)} onReview={onOpenThread ? () => onOpenThread(task.thread_id) : undefined} />
            ))}
          </ul>
        )}
      </Section>

      <form onSubmit={sendFeedback} className="flex gap-2">
        <Input value={feedback} onChange={(e) => setFeedback(e.target.value)} placeholder="Adjust it for next time, e.g. “focus on price changes”" aria-label="Feedback for the next run" />
        <Button type="submit" variant="primary" disabled={!feedback.trim()} aria-label="Send">
          <Send />
        </Button>
      </form>
      </>
    </Pane>
  );
}

function RunRow({ run, onRetry, onReview }: { run: ScheduledTaskRun; onRetry: () => void; onReview?: () => void }) {
  const [open, setOpen] = useState(false);
  const failed = run.status === "failed";
  const tone = failed ? "danger" : run.status === "waiting" ? "warning" : "success";
  const text = failed ? run.error_message || "The run failed." : run.was_silent ? "Checked; nothing to report." : run.output_summary;
  return (
    <li className="py-2 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 text-left">
          <ChevronDown className={`size-3.5 shrink-0 text-muted transition-transform ${open ? "" : "-rotate-90"}`} aria-hidden />
          <span className="text-sm text-foreground">{when(run.executed_at)}</span>
          <Badge tone={tone}>{run.was_silent ? "quiet" : run.status}</Badge>
          <span className="truncate text-xs text-muted">
            {run.duration_ms > 0 ? `${(run.duration_ms / 1000).toFixed(run.duration_ms < 10000 ? 1 : 0)}s` : ""}
            {(run.cost_usd ?? 0) > 0 ? ` · $${(run.cost_usd ?? 0).toFixed((run.cost_usd ?? 0) < 0.01 ? 4 : 2)}` : ""}
          </span>
        </button>
        {failed && (
          <Button size="sm" onClick={onRetry}>
            <RefreshCw /> Retry
          </Button>
        )}
        {run.status === "waiting" && onReview && (
          <Button size="sm" variant="primary" onClick={onReview}>
            Review
          </Button>
        )}
      </div>
      {open && (
        <div className={`mt-2 select-text rounded-lg border border-border p-3 text-sm ${failed ? "font-mono text-danger" : "prose-chat"}`}>
          {failed ? <p className="whitespace-pre-wrap text-xs">{text}</p> : <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>}
        </div>
      )}
    </li>
  );
}
