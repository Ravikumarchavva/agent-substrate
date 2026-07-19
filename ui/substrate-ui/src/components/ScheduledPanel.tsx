"use client";

import React, { useState, useEffect } from "react";
import { nanoid } from "nanoid";
import {
  CalendarClock,
  Play,
  Pause,
  Trash2,
  ArrowLeft,
  Send,
  Sparkles,
  AlertCircle,
  Clock,
  Loader2,
  Edit3,
  X,
  FileText,
  Activity,
  Bell,
  GraduationCap,
  RefreshCw,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api } from "@/lib/api";
import type { ScheduledTask, ScheduledTaskRun } from "@/types";

interface ScheduledPanelProps {
  onBack: () => void;
}

export function ScheduledPanel({ onBack }: ScheduledPanelProps) {
  // Navigation states
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [selectedTask, setSelectedTask] = useState<ScheduledTask | null>(null);
  const [runs, setRuns] = useState<ScheduledTaskRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingRuns, setLoadingRuns] = useState(false);

  // Parsing & creation states
  const [naturalText, setNaturalText] = useState("");
  const [isParsing, setIsParsing] = useState(false);
  const [showConfigModal, setShowConfigModal] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // New task config form (parsed from NLP or filled manually)
  const [newConfig, setNewConfig] = useState<{
    name: string;
    prompt: string;
    cron_expression: string;
    kind: "cron" | "interval";
    task_type: "report" | "monitor" | "reminder" | "learning";
    lookback_runs: number;
    auto_disable: boolean;
  }>({
    name: "",
    prompt: "",
    cron_expression: "0 8 * * *",
    kind: "cron",
    task_type: "report",
    lookback_runs: 5,
    auto_disable: false,
  });

  // Edit states for selected task
  const [isEditingPrompt, setIsEditingPrompt] = useState(false);
  const [editPromptValue, setEditPromptValue] = useState("");

  // Feedback input state
  const [feedbackText, setFeedbackText] = useState("");
  const [isSendingFeedback, setIsSendingFeedback] = useState(false);

  // Load task list on mount
  const fetchTasks = async () => {
    setLoading(true);
    setErrorMsg(null);
    try {
      const data = await api.getScheduledTasks();
      setTasks(data);
    } catch (err) {
      setErrorMsg((err instanceof Error && err.message) || "Failed to fetch scheduled tasks.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTasks();
  }, []);

  // Fetch runs when selected task changes
  useEffect(() => {
    if (selectedTask) {
      fetchRuns(selectedTask.id);
      setEditPromptValue(selectedTask.prompt);
      setIsEditingPrompt(false);
    }
  }, [selectedTask]);

  const fetchRuns = async (taskId: string) => {
    setLoadingRuns(true);
    try {
      const runData = await api.getScheduledTaskRuns(taskId, { limit: 50, include_silent: true });
      setRuns(runData);
    } catch (err) {
      console.error("Failed to fetch task runs:", err);
    } finally {
      setLoadingRuns(false);
    }
  };

  // Natural language query submit
  const handleNLPSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!naturalText.trim()) return;

    setIsParsing(true);
    setErrorMsg(null);
    try {
      const parsed = await api.parseScheduledTaskText(naturalText);
      setNewConfig({
        name: parsed.name,
        prompt: parsed.prompt,
        cron_expression: parsed.cron_expression,
        kind: parsed.kind,
        task_type: parsed.task_type,
        lookback_runs: 5,
        auto_disable: parsed.task_type === "monitor", // default monitor tasks to auto_disable if they are alert-oriented
      });
      setShowConfigModal(true);
    } catch (err) {
      setErrorMsg((err instanceof Error && err.message) || "Failed to interpret text. Please adjust and retry.");
    } finally {
      setIsParsing(false);
    }
  };

  // Create Task
  const handleCreateTask = async () => {
    setErrorMsg(null);
    try {
      await api.createScheduledTask({
        name: newConfig.name.trim(),
        prompt: newConfig.prompt.trim(),
        cron_expression: newConfig.cron_expression.trim(),
        kind: newConfig.kind,
        task_type: newConfig.task_type,
        lookback_runs: newConfig.lookback_runs,
        auto_disable: newConfig.auto_disable,
      });
      setShowConfigModal(false);
      setNaturalText("");
      fetchTasks();
    } catch (err) {
      setErrorMsg((err instanceof Error && err.message) || "Failed to create task.");
    }
  };

  // Run now manual trigger
  const handleRunNow = async (taskId: string, e?: React.MouseEvent) => {
    e?.stopPropagation();
    try {
      await api.runScheduledTaskNow(taskId);
      // Give a tiny delay then refresh if viewing this task's runs
      setTimeout(() => {
        if (selectedTask?.id === taskId) {
          fetchRuns(taskId);
        }
        fetchTasks();
      }, 1000);
    } catch (err) {
      console.error("Failed to trigger task run:", err);
    }
  };

  // Pause / Resume toggle
  const handleToggleStatus = async (task: ScheduledTask, e?: React.MouseEvent) => {
    e?.stopPropagation();
    const newStatus = task.status === "active" ? "paused" : "active";
    try {
      const updated = await api.updateScheduledTask(task.id, { status: newStatus });
      setTasks((prev) => prev.map((t) => (t.id === task.id ? updated : t)));
      if (selectedTask?.id === task.id) {
        setSelectedTask(updated);
      }
    } catch (err) {
      console.error("Failed to update status:", err);
    }
  };

  // Delete task
  const handleDeleteTask = async (taskId: string, e?: React.MouseEvent) => {
    e?.stopPropagation();
    if (!confirm("Permanently delete this scheduled task and all its run logs?")) return;

    try {
      await api.deleteScheduledTask(taskId);
      setTasks((prev) => prev.filter((t) => t.id !== taskId));
      if (selectedTask?.id === taskId) {
        setSelectedTask(null);
      }
    } catch (err) {
      console.error("Failed to delete task:", err);
    }
  };

  // Update prompt instructions
  const handleSavePrompt = async () => {
    if (!selectedTask) return;
    try {
      const updated = await api.updateScheduledTask(selectedTask.id, { prompt: editPromptValue });
      setSelectedTask(updated);
      setIsEditingPrompt(false);
      fetchTasks();
    } catch (err) {
      alert((err instanceof Error && err.message) || "Failed to save prompt override.");
    }
  };

  // Add feedback / reply to thread
  const handleSendFeedback = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedTask || !feedbackText.trim()) return;

    setIsSendingFeedback(true);
    try {
      await api.addScheduledTaskFeedback(selectedTask.id, feedbackText.trim());
      setFeedbackText("");
      // Append a virtual run output or visual confirm
      const virtualRun: ScheduledTaskRun = {
        id: nanoid(),
        task_id: selectedTask.id,
        status: "success",
        output_summary: `Feedback accepted: "${feedbackText.trim()}". The agent will adapt to this instruction on the next execution cycle.`,
        executed_at: new Date().toISOString(),
        duration_ms: 0,
        was_silent: false,
        error_message: null,
      };
      setRuns((prev) => [virtualRun, ...prev]);
    } catch (err) {
      alert((err instanceof Error && err.message) || "Failed to persist feedback.");
    } finally {
      setIsSendingFeedback(false);
    }
  };

  // Helper styles based on task types
  const getTypeBadgeStyles = (type: string) => {
    switch (type) {
      case "report":
        return "bg-emerald-500/10 text-emerald-500 border border-emerald-500/10";
      case "monitor":
        return "bg-cyan-500/10 text-cyan-500 border border-cyan-500/10";
      case "reminder":
        return "bg-amber-500/10 text-amber-500 border border-amber-500/10";
      case "learning":
        return "bg-violet-500/10 text-violet-500 border border-violet-500/10";
      default:
        return "bg-neutral-500/10 text-neutral-500 border border-neutral-500/10";
    }
  };

  const getTypeIcon = (type: string) => {
    switch (type) {
      case "report":
        return <FileText className="w-4 h-4" />;
      case "monitor":
        return <Activity className="w-4 h-4" />;
      case "reminder":
        return <Bell className="w-4 h-4" />;
      case "learning":
        return <GraduationCap className="w-4 h-4" />;
      default:
        return <CalendarClock className="w-4 h-4" />;
    }
  };

  const getStatusPill = (status: string) => {
    switch (status) {
      case "active":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/10 text-emerald-500 border border-emerald-500/10 select-none uppercase tracking-wider">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
            Active
          </span>
        );
      case "paused":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/10 text-amber-500 border border-amber-500/10 select-none uppercase tracking-wider">
            <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
            Paused
          </span>
        );
      case "error":
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-rose-500/10 text-rose-500 border border-rose-500/10 select-none uppercase tracking-wider">
            <span className="w-1.5 h-1.5 rounded-full bg-rose-500" />
            Error
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-bold bg-neutral-500/10 text-neutral-500 border border-neutral-500/10 select-none uppercase tracking-wider">
            {status}
          </span>
        );
    }
  };

  const formatScheduleText = (task: ScheduledTask) => {
    if (task.kind === "interval") {
      const secs = Number.parseInt(task.cron_expression);
      if (secs >= 86400) return `Every ${Math.round(secs / 86400)} day(s)`;
      if (secs >= 3600) return `Every ${Math.round(secs / 3600)} hour(s)`;
      if (secs >= 60) return `Every ${Math.round(secs / 60)} minute(s)`;
      return `Every ${secs} second(s)`;
    }
    return `Cron: ${task.cron_expression}`;
  };

  return (
    <div className="flex h-full w-full flex-col bg-(--background) text-(--foreground) relative overflow-hidden">
      {/* ─── Main Header ─── */}
      <div className="flex items-center justify-between border-b border-(--border) px-6 py-4 bg-(--card)/50 backdrop-blur-md sticky top-0 z-10">
        <div className="flex items-center gap-3">
          {selectedTask ? (
            <button
              onClick={() => setSelectedTask(null)}
              className="p-2 -ml-2 rounded-xl hover:bg-(--card-hover) transition-all duration-200 cursor-pointer text-(--muted) hover:text-(--foreground) btn-icon"
            >
              <ArrowLeft className="w-5 h-5" />
            </button>
          ) : (
            <button
              onClick={onBack}
              className="p-2 -ml-2 rounded-xl hover:bg-(--card-hover) transition-all duration-200 cursor-pointer text-(--muted) hover:text-(--foreground) btn-icon"
            >
              <ArrowLeft className="w-5 h-5" />
            </button>
          )}
          <div>
            <h1 className="text-base font-bold flex items-center gap-2">
              <CalendarClock className="w-5 h-5 text-(--muted)" />
              {selectedTask ? selectedTask.name : "Scheduled Tasks"}
            </h1>
            <p className="text-xs text-(--muted)">
              {selectedTask
                ? `${selectedTask.task_type.toUpperCase()} • ${formatScheduleText(selectedTask)}`
                : "Automate agent loops and background processes"}
            </p>
          </div>
        </div>

        {!selectedTask && (
          <button
            onClick={fetchTasks}
            className="p-2 rounded-xl hover:bg-(--card-hover) transition-colors cursor-pointer text-(--muted) hover:text-(--foreground) btn-icon border border-(--border)"
            title="Refresh list"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        )}
      </div>

      {errorMsg && (
        <div className="mx-6 mt-4 p-3.5 rounded-xl border border-rose-500/20 bg-rose-500/10 text-rose-400 text-xs flex items-center gap-2.5 animate-in fade-in slide-in-from-top-2 duration-200">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{errorMsg}</span>
        </div>
      )}

      {/* ─── View 1: Task List ─── */}
      {!selectedTask ? (
        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-6">
          {/* Scheduling Input Bar */}
          <form onSubmit={handleNLPSubmit} className="relative substrate-fade-up max-w-3xl mx-auto">
            <div className="relative flex items-center overflow-hidden rounded-2xl border border-(--border) bg-(--card) shadow-md focus-within:border-violet-500/50 focus-within:ring-1 focus-within:ring-violet-500/20 transition-all duration-300">
              <Sparkles className="absolute left-4 w-4 h-4 text-violet-500 animate-pulse" />
              <input
                type="text"
                value={naturalText}
                onChange={(e) => setNaturalText(e.target.value)}
                placeholder="Ask to schedule a task... (e.g. 'every day at 8am tell me AI news')"
                className="w-full pl-11 pr-24 py-3.5 bg-transparent border-0 rounded-none text-sm outline-none placeholder:text-(--muted)"
              />
              <div className="absolute right-2 flex gap-1">
                {isParsing ? (
                  <div className="px-3 py-1.5 text-xs text-(--muted) flex items-center gap-1.5 bg-(--card-hover) rounded-xl">
                    <Loader2 className="w-3.5 h-3.5 animate-spin text-violet-500" />
                    <span>Parsing...</span>
                  </div>
                ) : (
                  <button
                    type="submit"
                    disabled={!naturalText.trim()}
                    className="px-4 py-1.5 rounded-xl bg-foreground text-background font-semibold text-xs hover:opacity-90 disabled:opacity-30 disabled:pointer-events-none transition-all cursor-pointer flex items-center gap-1 min-h-unset min-w-unset btn-icon shadow-sm"
                  >
                    <span>Schedule</span>
                  </button>
                )}
              </div>
            </div>
          </form>

          {loading ? (
            <div className="flex flex-col items-center justify-center py-20 text-(--muted) gap-2">
              <Loader2 className="w-8 h-8 animate-spin text-violet-500" />
              <span className="text-xs font-semibold uppercase tracking-wider">Loading schedules...</span>
            </div>
          ) : tasks.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 px-6 border border-(--border) rounded-2xl bg-(--card) shadow-sm max-w-xl mx-auto text-center substrate-fade-up">
              <div className="w-16 h-16 rounded-2xl bg-violet-500/10 flex items-center justify-center mb-6 text-violet-500">
                <CalendarClock className="w-8 h-8" />
              </div>
              <h3 className="font-bold text-sm text-(--foreground)">No scheduled tasks yet</h3>
              <p className="text-xs text-(--muted) mt-2 max-w-md leading-relaxed">
                Automate your AI assistant to run background updates, monitor metrics, or deliver daily reports. Use the scheduling composer above to start.
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {tasks.map((task, idx) => (
                <div
                  key={task.id}
                  onClick={() => setSelectedTask(task)}
                  style={{ "--stagger": idx } as React.CSSProperties}
                  className="p-5 rounded-2xl border border-(--border) bg-(--card) hover:border-(--border-hover) hover:shadow-md transition-all duration-300 flex flex-col justify-between group cursor-pointer substrate-hover-lift"
                >
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <div className={`p-2.5 rounded-xl shrink-0 flex items-center justify-center ${getTypeBadgeStyles(task.task_type)}`}>
                        {getTypeIcon(task.task_type)}
                      </div>
                      {getStatusPill(task.status)}
                    </div>
                    <div className="space-y-1">
                      <h3 className="font-bold text-sm group-hover:text-violet-500 transition-colors">
                        {task.name}
                      </h3>
                      <p className="text-xs text-(--muted) line-clamp-2 leading-relaxed">
                        {task.prompt}
                      </p>
                    </div>
                  </div>

                  <div className="mt-5 pt-4 border-t border-(--border) flex items-center justify-between text-xs text-(--muted)">
                    <div className="flex flex-col gap-0.5">
                      <span className="font-semibold text-(--foreground)">{formatScheduleText(task)}</span>
                      {task.next_run_at && (
                        <span>Next: {new Date(task.next_run_at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span>
                      )}
                    </div>

                    <div className="flex items-center gap-1.5 opacity-80 group-hover:opacity-100 transition-opacity">
                      <button
                        onClick={(e) => handleRunNow(task.id, e)}
                        className="p-2 rounded-lg bg-(--step-bg) hover:bg-emerald-500/10 hover:text-emerald-500 border border-transparent hover:border-emerald-500/20 transition-all cursor-pointer btn-icon"
                        title="Run now"
                      >
                        <Play className="w-3.5 h-3.5 fill-current" />
                      </button>
                      <button
                        onClick={(e) => handleToggleStatus(task, e)}
                        className={`p-2 rounded-lg bg-(--step-bg) border border-transparent transition-all cursor-pointer btn-icon ${
                          task.status === "active"
                            ? "hover:bg-amber-500/10 hover:text-amber-500 hover:border-amber-500/20"
                            : "hover:bg-emerald-500/10 hover:text-emerald-500 hover:border-emerald-500/20"
                        }`}
                        title={task.status === "active" ? "Pause schedule" : "Activate schedule"}
                      >
                        {task.status === "active" ? (
                          <Pause className="w-3.5 h-3.5 fill-current" />
                        ) : (
                          <Play className="w-3.5 h-3.5" />
                        )}
                      </button>
                      <button
                        onClick={(e) => handleDeleteTask(task.id, e)}
                        className="p-2 rounded-lg bg-(--step-bg) hover:bg-rose-500/10 hover:text-rose-500 border border-transparent hover:border-rose-500/20 transition-all cursor-pointer btn-icon"
                        title="Delete task"
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : (
        /* ─── View 2: History & Execution Feed ─── */
        <div className="flex-1 flex flex-col overflow-hidden">
          {/* Config Detail Dashboard Banner */}
          <div className="px-6 py-5 border-b border-(--border) bg-(--card)/30 space-y-5">
            <div className="flex flex-col md:flex-row md:items-start justify-between gap-5">
              <div className="flex-1 space-y-2">
                <span className="text-[10px] font-bold uppercase tracking-wider text-(--muted) flex items-center gap-1.5">
                  <span className={`p-1.5 rounded-lg shrink-0 flex items-center justify-center ${getTypeBadgeStyles(selectedTask.task_type)}`}>
                    {getTypeIcon(selectedTask.task_type)}
                  </span>
                  {selectedTask.task_type} Instructions
                </span>
                {isEditingPrompt ? (
                  <div className="space-y-2">
                    <textarea
                      value={editPromptValue}
                      onChange={(e) => setEditPromptValue(e.target.value)}
                      rows={4}
                      className="w-full p-3.5 text-sm bg-(--input-bg) border border-(--border) rounded-xl focus:outline-none focus:border-violet-500/50"
                    />
                    <div className="flex gap-2 justify-end">
                      <button
                        onClick={() => setIsEditingPrompt(false)}
                        className="px-4 py-1.5 text-xs font-semibold rounded-xl border border-(--border) hover:bg-(--card-hover) cursor-pointer min-h-unset min-w-unset"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={handleSavePrompt}
                        className="px-4 py-1.5 text-xs font-bold rounded-xl bg-foreground text-background cursor-pointer min-h-unset min-w-unset"
                      >
                        Save Prompt
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-start gap-2.5 group">
                    <p className="text-sm font-semibold text-(--foreground)/90 leading-relaxed pr-6 select-text">
                      {selectedTask.prompt}
                    </p>
                    <button
                      onClick={() => setIsEditingPrompt(true)}
                      className="p-1.5 rounded-lg hover:bg-(--card-hover) border border-transparent hover:border-(--border) opacity-0 group-hover:opacity-100 transition-all btn-icon text-(--muted)"
                      title="Edit instructions"
                    >
                      <Edit3 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                )}
              </div>

              {/* Task Detail Summary Widgets */}
              <div className="shrink-0 flex flex-wrap md:flex-col items-start md:items-end gap-3 text-xs">
                <div className="flex gap-2">
                  <button
                    onClick={() => handleRunNow(selectedTask.id)}
                    className="px-4 py-2 rounded-xl bg-emerald-500 text-white font-bold hover:bg-emerald-600 shadow-sm shadow-emerald-500/10 transition-colors flex items-center gap-1.5 cursor-pointer min-h-unset min-w-unset"
                  >
                    <Play className="w-3.5 h-3.5 fill-current" />
                    <span>Run Now</span>
                  </button>
                  <button
                    onClick={() => handleToggleStatus(selectedTask)}
                    className={`px-4 py-2 rounded-xl font-bold border border-(--border) bg-(--card) transition-all cursor-pointer min-h-unset min-w-unset hover:bg-(--card-hover) ${
                      selectedTask.status === "active" ? "hover:text-amber-500" : "hover:text-emerald-500"
                    }`}
                  >
                    {selectedTask.status === "active" ? "Pause" : "Activate"}
                  </button>
                </div>
              </div>
            </div>

            {/* Horizontal Dashboard Stats */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-3 border-t border-(--border)">
              <div className="p-3 bg-(--card) border border-(--border) rounded-xl space-y-1">
                <div className="text-[10px] font-bold uppercase tracking-wider text-(--muted)">Status</div>
                <div className="flex items-center mt-1">{getStatusPill(selectedTask.status)}</div>
              </div>
              <div className="p-3 bg-(--card) border border-(--border) rounded-xl space-y-1">
                <div className="text-[10px] font-bold uppercase tracking-wider text-(--muted)">Schedule</div>
                <div className="text-xs font-bold text-(--foreground) truncate mt-0.5">{formatScheduleText(selectedTask)}</div>
              </div>
              <div className="p-3 bg-(--card) border border-(--border) rounded-xl space-y-1">
                <div className="text-[10px] font-bold uppercase tracking-wider text-(--muted)">Lookback Context</div>
                <div className="text-xs font-bold text-(--foreground) mt-0.5">{selectedTask.lookback_runs} Runs</div>
              </div>
              <div className="p-3 bg-(--card) border border-(--border) rounded-xl space-y-1">
                <div className="text-[10px] font-bold uppercase tracking-wider text-(--muted)">Next Execution</div>
                <div className="text-xs font-bold text-(--foreground) truncate mt-0.5">
                  {selectedTask.next_run_at
                    ? new Date(selectedTask.next_run_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                    : "—"}
                </div>
              </div>
            </div>
          </div>

          {/* Timeline Execution Logs Feed */}
          <div className="flex-1 overflow-y-auto px-6 py-6 space-y-6">
            <div className="max-w-4xl mx-auto space-y-6">
              <h2 className="text-[10px] font-bold uppercase tracking-wider text-(--muted) mb-2">Run Logs & Timeline</h2>
              
              {loadingRuns ? (
                <div className="flex items-center justify-center py-20 text-(--muted) gap-2.5">
                  <Loader2 className="w-6 h-6 animate-spin text-violet-500" />
                  <span className="text-xs font-semibold uppercase tracking-wider">Loading timeline logs...</span>
                </div>
              ) : runs.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-16 border border-dashed border-(--border) rounded-2xl bg-(--card)/10 text-(--muted) text-center max-w-md mx-auto">
                  <Clock className="w-8 h-8 text-(--muted-foreground) mb-3 animate-pulse" />
                  <p className="text-sm font-bold text-(--foreground)">No run history yet</p>
                  <p className="text-xs text-(--muted) mt-1">Click &quot;Run Now&quot; to trigger the initial background process manually.</p>
                </div>
              ) : (
                <div className="space-y-6 relative pl-4 border-l border-(--border)">
                  {runs.map((run, idx) => {
                    const dateStr = new Date(run.executed_at).toLocaleString([], {
                      month: 'short',
                      day: 'numeric',
                      hour: '2-digit',
                      minute: '2-digit'
                    });

                    if (run.was_silent) {
                      return (
                        <div
                          key={run.id}
                          style={{ "--stagger": idx } as React.CSSProperties}
                          className="relative pl-6 py-1 text-xs text-(--muted) flex items-center gap-2.5 substrate-fade-up"
                        >
                          <div className="absolute -left-[23px] top-1/2 -translate-y-1/2 w-2.5 h-2.5 rounded-full bg-(--border) border-4 border-(--background)" />
                          <span className="font-bold text-(--foreground)/80">{dateStr}</span>
                          <span className="text-(--muted-foreground)">•</span>
                          <span className="italic">Silent check completed. Metrics within normal bounds.</span>
                        </div>
                      );
                    }

                    const isFailed = run.status === "failed";
                    return (
                      <div
                        key={run.id}
                        style={{ "--stagger": idx } as React.CSSProperties}
                        className="relative pl-6 substrate-fade-up"
                      >
                        {/* Timeline dot */}
                        <div
                          className={`absolute -left-[24px] top-4.5 w-3.5 h-3.5 rounded-full border-4 border-(--background) ${
                            isFailed ? "bg-rose-500 shadow-lg shadow-rose-500/20" : "bg-emerald-500 shadow-lg shadow-emerald-500/20"
                          }`}
                        />

                        {/* Log Card */}
                        <div className="rounded-2xl border border-(--border) bg-(--card) shadow-sm hover:shadow-md transition-shadow overflow-hidden">
                          {/* Log Header */}
                          <div className="flex justify-between items-center px-4 py-3 bg-(--card-hover)/40 border-b border-(--border) text-xs">
                            <span className="font-bold text-(--foreground)">{dateStr}</span>
                            <div className="flex items-center gap-2.5 text-(--muted)">
                              {run.duration_ms > 0 && <span>Duration: {run.duration_ms}ms</span>}
                              <span
                                className={`px-2.5 py-0.5 rounded-full text-[9px] font-bold tracking-wider uppercase border ${
                                  isFailed 
                                    ? "bg-rose-500/10 text-rose-500 border-rose-500/10" 
                                    : "bg-emerald-500/10 text-emerald-500 border-emerald-500/10"
                                }`}
                              >
                                {run.status}
                              </span>
                            </div>
                          </div>

                          {/* Log Content */}
                          <div className="p-4">
                            {isFailed ? (
                              <div className="text-xs text-rose-400 p-4 rounded-xl border border-rose-500/15 bg-rose-500/5 font-mono select-text whitespace-pre-wrap leading-relaxed">
                                {run.error_message || "Agent execution failed unexpectedly."}
                              </div>
                            ) : (
                              <div className="prose-chat select-text">
                                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                  {run.output_summary}
                                </ReactMarkdown>
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>

          {/* Feedback reply container */}
          <div className="px-6 py-4 border-t border-(--border) bg-(--card)/40 backdrop-blur-md sticky bottom-0 z-10">
            <div className="max-w-3xl mx-auto">
              <form onSubmit={handleSendFeedback} className="flex gap-2">
                <input
                  type="text"
                  value={feedbackText}
                  onChange={(e) => setFeedbackText(e.target.value)}
                  placeholder="Adjust instructions for next run... (e.g. 'prioritize stock price comparison')"
                  className="flex-1 px-4 py-2.5 bg-(--input-bg) border border-(--border) rounded-xl text-sm focus:outline-none focus:border-violet-500/50"
                />
                <button
                  type="submit"
                  disabled={!feedbackText.trim() || isSendingFeedback}
                  className="px-5 py-2.5 bg-foreground text-background rounded-xl text-sm font-bold hover:opacity-90 transition-all flex items-center justify-center disabled:opacity-30 disabled:pointer-events-none cursor-pointer shadow-sm min-h-unset min-w-unset btn-icon"
                >
                  {isSendingFeedback ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Send className="w-4 h-4" />
                  )}
                </button>
              </form>
              <p className="text-[10px] text-(--muted) mt-2 pl-1 select-none leading-relaxed">
                Feedback instructions are saved to thread memory. The agent reviews past instructions and output histories on each schedule cycle.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* ─── Config Edit / Parsing Confirmation Modal ─── */}
      {showConfigModal && (
        <div className="absolute inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-(--panel) border border-(--panel-border) rounded-2xl max-w-lg w-full p-6 shadow-2xl flex flex-col gap-4 animate-in zoom-in-95 duration-200 select-none">
            <div className="flex justify-between items-center border-b border-(--border) pb-3.5">
              <h2 className="text-sm font-bold flex items-center gap-2">
                <Sparkles className="w-4.5 h-4.5 text-violet-500" />
                Confirm Task Schedule
              </h2>
              <button
                onClick={() => setShowConfigModal(false)}
                className="p-1 rounded-lg hover:bg-(--card-hover) cursor-pointer btn-icon"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="space-y-4 text-xs overflow-y-auto max-h-[55vh] pr-1">
              <div className="space-y-1">
                <label className="text-[10px] font-bold text-(--muted) uppercase tracking-wider">Task Name</label>
                <input
                  type="text"
                  value={newConfig.name}
                  onChange={(e) => setNewConfig((c) => ({ ...c, name: e.target.value }))}
                  className="w-full p-2.5 bg-(--input-bg) border border-(--border) rounded-xl text-xs focus:outline-none"
                />
              </div>

              <div className="space-y-1">
                <label className="text-[10px] font-bold text-(--muted) uppercase tracking-wider">Instructions (Agent Prompt)</label>
                <textarea
                  value={newConfig.prompt}
                  onChange={(e) => setNewConfig((c) => ({ ...c, prompt: e.target.value }))}
                  rows={4}
                  className="w-full p-2.5 bg-(--input-bg) border border-(--border) rounded-xl text-xs focus:outline-none"
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-1">
                  <label className="text-[10px] font-bold text-(--muted) uppercase tracking-wider">Schedule Type</label>
                  <select
                    value={newConfig.kind}
                    onChange={(e) =>
                      setNewConfig((c) => ({ ...c, kind: e.target.value as "cron" | "interval" }))
                    }
                    className="w-full p-2.5 bg-(--input-bg) border border-(--border) rounded-xl text-xs focus:outline-none"
                  >
                    <option value="cron">Cron Expression</option>
                    <option value="interval">Interval (Seconds)</option>
                  </select>
                </div>

                <div className="space-y-1">
                  <label className="text-[10px] font-bold text-(--muted) uppercase tracking-wider">Expression / Value</label>
                  <input
                    type="text"
                    value={newConfig.cron_expression}
                    onChange={(e) => setNewConfig((c) => ({ ...c, cron_expression: e.target.value }))}
                    className="w-full p-2.5 bg-(--input-bg) border border-(--border) rounded-xl text-xs focus:outline-none"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-1">
                  <label className="text-[10px] font-bold text-(--muted) uppercase tracking-wider">Task Type</label>
                  <select
                    value={newConfig.task_type}
                    onChange={(e) =>
                      setNewConfig((c) => ({
                        ...c,
                        task_type: e.target.value as "report" | "monitor" | "reminder" | "learning",
                      }))
                    }
                    className="w-full p-2.5 bg-(--input-bg) border border-(--border) rounded-xl text-xs focus:outline-none"
                  >
                    <option value="report">Report / News Digest</option>
                    <option value="monitor">Alert Monitor</option>
                    <option value="reminder">Reminder Pings</option>
                    <option value="learning">Continuous learning</option>
                  </select>
                </div>

                <div className="space-y-1">
                  <label className="text-[10px] font-bold text-(--muted) uppercase tracking-wider">Context Lookback (Runs)</label>
                  <input
                    type="number"
                    min={0}
                    max={20}
                    value={newConfig.lookback_runs}
                    onChange={(e) =>
                      setNewConfig((c) => ({ ...c, lookback_runs: Number.parseInt(e.target.value, 10) || 0 }))
                    }
                    className="w-full p-2.5 bg-(--input-bg) border border-(--border) rounded-xl text-xs focus:outline-none"
                  />
                </div>
              </div>

              <div className="flex items-center gap-2 pt-2">
                <input
                  type="checkbox"
                  id="auto_disable"
                  checked={newConfig.auto_disable}
                  onChange={(e) => setNewConfig((c) => ({ ...c, auto_disable: e.target.checked }))}
                  className="rounded border-(--border) text-violet-500 focus:ring-violet-500"
                />
                <label htmlFor="auto_disable" className="text-xs text-(--muted) font-medium cursor-pointer">
                  Auto-disable after first success (one-shot alert)
                </label>
              </div>
            </div>

            <div className="flex gap-2 justify-end border-t border-(--border) pt-4 mt-2">
              <button
                onClick={() => setShowConfigModal(false)}
                className="px-4 py-2 rounded-xl border border-(--border) hover:bg-(--card-hover) text-xs font-semibold cursor-pointer min-h-unset min-w-unset"
              >
                Discard
              </button>
              <button
                onClick={handleCreateTask}
                className="px-4 py-2 rounded-xl bg-foreground text-background text-xs font-bold cursor-pointer min-h-unset min-w-unset"
              >
                Confirm & Create
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default ScheduledPanel;
