"use client";

import type { ReactNode } from "react";
import { Globe2, Loader2, Sparkles } from "lucide-react";

function Notice({
  tone,
  children,
}: {
  tone: "success" | "error" | "info";
  children: ReactNode;
}) {
  const toneClass =
    tone === "success"
      ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-200"
      : tone === "error"
        ? "border-rose-500/25 bg-rose-500/10 text-rose-200"
        : "border-(--border) bg-(--card) text-(--muted)";

  return (
    <div className={`rounded-xl border px-4 py-3 text-sm ${toneClass}`}>
      {children}
    </div>
  );
}

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-4">
      <div>
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
        {description && (
          <p className="mt-1 text-xs text-(--muted)">{description}</p>
        )}
      </div>
      {children}
    </section>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block space-y-1.5">
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs font-medium text-(--muted)">{label}</span>
        {hint && <span className="text-[11px] text-(--muted)">{hint}</span>}
      </div>
      {children}
    </label>
  );
}

function FeatureCard({
  icon,
  title,
  description,
}: {
  icon: ReactNode;
  title: string;
  description: string;
}) {
  return (
    <div
      className="rounded-[22px] p-5"
      style={{ background: "var(--card)", boxShadow: "var(--shadow-sm)" }}
    >
      <div className="mb-4 flex h-11 w-11 items-center justify-center rounded-2xl bg-(--badge-bg) text-foreground">
        {icon}
      </div>
      <h4 className="text-sm font-semibold text-foreground">{title}</h4>
      <p className="mt-2 text-sm leading-6 text-(--muted)">{description}</p>
    </div>
  );
}

interface GeneralTabProps {
  customInstructions: string;
  setCustomInstructions: (v: string) => void;
  isSaving: boolean;
  saveError: string | null;
  setSaveError: (v: string | null) => void;
  saveSuccess: boolean;
  handleSaveInstructions: () => void;
  timezone: string;
  onTimezoneChange: (v: string) => void;
}

export function GeneralTab({
  customInstructions,
  setCustomInstructions,
  isSaving,
  saveError,
  setSaveError,
  saveSuccess,
  handleSaveInstructions,
  timezone,
  onTimezoneChange,
}: GeneralTabProps) {
  return (
    <div className="space-y-8">
      <div className="space-y-2">
        <h2 className="text-3xl font-semibold tracking-tight text-foreground">General</h2>
        <p className="max-w-2xl text-sm leading-6 text-(--muted)">
          Personal defaults that shape every conversation before tools, models, or connectors take over.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-3">
        <FeatureCard
          icon={<Globe2 className="h-5 w-5" />}
          title="Timezone memory"
          description="Calendar actions and time-aware prompts inherit this automatically."
        />
        <FeatureCard
          icon={<Sparkles className="h-5 w-5" />}
          title="Instruction memory"
          description="Your custom instructions act like a standing preference for all new chats."
        />
        <div
          className="rounded-[22px] p-5"
          style={{ background: "var(--card)", boxShadow: "var(--shadow-sm)" }}
        >
          <div className="text-[11px] font-semibold uppercase tracking-[0.16em] text-(--muted)">Summary</div>
          <p className="mt-4 text-sm text-(--muted)">Current timezone</p>
          <p className="mt-1 text-lg font-semibold text-foreground">{timezone || "Not set yet"}</p>
          <p className="mt-4 text-sm text-(--muted)">
            {customInstructions.trim() ? "Custom instructions are active." : "No custom instructions saved yet."}
          </p>
        </div>
      </div>

      <Section title="User preferences" description="Applied automatically to every conversation.">
        <Field label="Timezone" hint="IANA format recommended">
          <input
            type="text"
            value={timezone}
            onChange={(e) => onTimezoneChange(e.target.value)}
            placeholder="e.g. Asia/Kolkata"
            list="common-timezones"
            className="w-full rounded-xl border border-(--border) bg-background px-3 py-2.5 text-sm text-foreground outline-none transition focus:ring-2 focus:ring-(--accent)"
          />
          <datalist id="common-timezones">
            <option value="Asia/Kolkata" />
            <option value="America/New_York" />
            <option value="America/Chicago" />
            <option value="America/Los_Angeles" />
            <option value="Europe/London" />
            <option value="Europe/Paris" />
            <option value="Asia/Tokyo" />
            <option value="Asia/Singapore" />
            <option value="Australia/Sydney" />
            <option value="UTC" />
          </datalist>
          {timezone && (
            <p className="mt-1.5 text-xs text-(--muted)">Saved — will be used for calendar events and time-aware tasks.</p>
          )}
        </Field>
      </Section>

      <div className="h-px bg-(--border)" />

      <Section title="Custom instructions" description="Appended to the system prompt to shape tone and context.">
        <textarea
          value={customInstructions}
          onChange={(e) => { setCustomInstructions(e.target.value); setSaveError(null); }}
          rows={6}
          className="w-full resize-none rounded-xl border border-(--border) bg-background p-4 text-sm leading-7 outline-none transition focus:ring-2 focus:ring-(--accent)"
          placeholder="e.g. Always respond in British English. Keep answers concise."
        />
        {saveError && <Notice tone="error">{saveError}</Notice>}
        {saveSuccess && <Notice tone="success">Instructions saved.</Notice>}
        <div className="flex items-center gap-2">
          <button
            onClick={handleSaveInstructions}
            disabled={isSaving}
            className="inline-flex items-center gap-2 rounded-xl bg-(--accent) px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50 cursor-pointer"
          >
            {isSaving && <Loader2 className="h-4 w-4 animate-spin" />}
            {isSaving ? "Checking…" : "Save"}
          </button>
          {customInstructions && (
            <button
              onClick={() => { setCustomInstructions(""); localStorage.removeItem("system_instructions_override"); setSaveError(null); }}
              className="rounded-xl border border-(--border) px-4 py-2 text-sm font-medium transition-colors hover:bg-(--card-hover) cursor-pointer"
            >
              Clear
            </button>
          )}
        </div>
      </Section>
    </div>
  );
}
