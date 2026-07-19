"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ChevronDown, ChevronRight, Check, Info } from "lucide-react";
import type { ModelOption } from "@/types";

type Props = {
  models: ModelOption[];
  selectedModel: string;
  onSelectModel: (id: string) => void;
  thinkingLevel: string;
  onSelectThinking: (level: string) => void;
};

const EFFORT_META: Record<string, { label: string; desc?: string; info?: boolean }> = {
  low: { label: "Low", desc: "Default" },
  medium: { label: "Medium" },
  high: { label: "High" },
  xhigh: { label: "Max", info: true },
};

const PRIMARY_COUNT = 2;

export function ModelEffortPicker({
  models,
  selectedModel,
  onSelectModel,
  thinkingLevel,
  onSelectThinking,
}: Props) {
  const [open, setOpen] = useState(false);
  const [flyout, setFlyout] = useState<"none" | "effort" | "more">("none");
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ right: number; bottom: number } | null>(null);
  const [layout, setLayout] = useState<"left" | "right" | "top">("right");

  const MAIN_W = 240; // w-60
  const FLYOUT_W = 264; // ~w-64 + gap

  const enabled = models.filter((m) => !m.disabled);
  const primary = enabled.slice(0, PRIMARY_COUNT);
  const more = models.slice(PRIMARY_COUNT); // include disabled (shown greyed)
  const active = models.find((m) => m.id === selectedModel);
  const effortLevels = (active?.thinkingLevels ?? ["off", "low", "medium", "high"]).filter(
    (l) => l !== "off"
  );
  const thinkingOn = thinkingLevel !== "off";
  const levelLabel = thinkingOn ? EFFORT_META[thinkingLevel]?.label ?? thinkingLevel : "Low";

  useLayoutEffect(() => {
    if (!open || !triggerRef.current) return;
    const r = triggerRef.current.getBoundingClientRect();
    // Main panel hangs from the trigger's right edge, opening upward.
    setPos({ right: window.innerWidth - r.right, bottom: window.innerHeight - r.top + 8 });
    
    if (window.innerWidth < 640) {
      setLayout("top");
    } else {
      const fitsRight = r.right + FLYOUT_W <= window.innerWidth - 8;
      setLayout(fitsRight ? "right" : "left");
    }
  }, [open]);

  function close() {
    setOpen(false);
    setFlyout("none");
  }

  // Capture phase so a stopPropagation() in the chat area can't swallow the
  // outside click and leave the menu stuck open.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (triggerRef.current?.contains(t) || panelRef.current?.contains(t)) return;
      close();
    };
    document.addEventListener("mousedown", onDown, true);
    return () => document.removeEventListener("mousedown", onDown, true);
  }, [open]);
  const pickModel = (id: string) => {
    onSelectModel(id);
    close();
  };
  const pickEffort = (level: string) => {
    onSelectThinking(level);
    close();
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => (open ? close() : setOpen(true))}
        className="btn-icon flex cursor-pointer items-center gap-1 rounded-lg px-2 py-1 text-[12.5px] font-medium transition-colors hover:bg-(--card-hover)"
      >
        <span className="truncate text-foreground/90">{active?.label ?? selectedModel.split("/").pop()}</span>
        <span className="text-(--muted)">{levelLabel}</span>
        <ChevronDown className="h-3.5 w-3.5 text-(--muted)" />
      </button>

      {open && pos &&
        createPortal(
          <div
            ref={panelRef}
            className="fixed z-[100]"
            style={{ right: pos.right, bottom: pos.bottom }}
          >
            {/* Main panel — relative anchor; flyout floats off its side */}
            <div
              className="substrate-scale-in relative flex w-60 flex-col gap-0.5 rounded-2xl p-1.5 shadow-2xl"
              style={{ background: "var(--card)", border: "1px solid var(--border)", transformOrigin: "bottom right" }}
            >
              {primary.map((m) => (
                <ModelRow
                  key={m.id}
                  model={m}
                  active={m.id === selectedModel}
                  onClick={() => pickModel(m.id)}
                  onHover={() => setFlyout("none")}
                />
              ))}

              <div className="my-1 h-px bg-(--border)" />

              <FlyoutRow
                label="Effort"
                value={levelLabel}
                open={flyout === "effort"}
                onOpen={() => setFlyout("effort")}
              />
              {more.length > 0 && (
                <FlyoutRow
                  label="More models"
                  open={flyout === "more"}
                  onOpen={() => setFlyout("more")}
                />
              )}

              {/* Flyout — floats to the right (default), left, or top (mobile) */}
              {flyout !== "none" && (
                <div
                  className={`absolute ${
                    layout === "top"
                      ? "bottom-full mb-2 right-0"
                      : layout === "left"
                      ? "bottom-0 right-full mr-2"
                      : "bottom-0 left-full ml-2"
                  }`}
                >
                  {flyout === "effort" ? (
                    <div
                      className="substrate-scale-in flex w-64 flex-col rounded-2xl p-1.5 shadow-2xl"
                      style={{ background: "var(--card)", border: "1px solid var(--border)", transformOrigin: layout === "right" ? "bottom left" : "bottom right" }}
                    >
                      <p className="px-3 pb-1.5 pt-2 text-[12px] leading-snug text-(--muted)">
                        Higher effort means more thorough responses, but takes longer and uses your limits faster.
                      </p>
                      {effortLevels.map((level) => {
                        const meta = EFFORT_META[level];
                        const isActive = thinkingOn && thinkingLevel === level;
                        return (
                          <button
                            key={level}
                            type="button"
                            onClick={() => pickEffort(level)}
                            className="btn-icon flex w-full items-center justify-between rounded-xl px-3 py-2.5 text-left transition-colors hover:bg-(--card-hover)"
                          >
                            <span className="flex items-center gap-2">
                              <span className={`text-[13.5px] font-medium ${isActive ? "text-foreground" : "text-foreground/85"}`}>
                                {meta?.label ?? level}
                              </span>
                              {meta?.desc && <span className="text-[11px] text-(--muted)">{meta.desc}</span>}
                              {meta?.info && <Info className="h-3.5 w-3.5 text-(--muted)" />}
                            </span>
                            {isActive && <Check className="h-4 w-4 text-blue-500" strokeWidth={2.5} />}
                          </button>
                        );
                      })}

                      <div className="my-1 h-px bg-(--border)" />

                      <div className="flex items-center justify-between gap-3 rounded-xl px-3 py-2.5">
                        <div>
                          <div className="text-[13.5px] font-medium text-foreground/90">Thinking</div>
                          <div className="text-[11px] text-(--muted)">Can think for more complex tasks</div>
                        </div>
                        <Toggle
                          on={thinkingOn}
                          onClick={() => onSelectThinking(thinkingOn ? "off" : effortLevels[0] ?? "low")}
                        />
                      </div>
                    </div>
                  ) : (
                    <div
                      className="substrate-scale-in flex w-56 flex-col gap-0.5 rounded-2xl p-1.5 shadow-2xl"
                      style={{ background: "var(--card)", border: "1px solid var(--border)", transformOrigin: layout === "right" ? "bottom left" : "bottom right" }}
                    >
                      {more.map((m) => (
                        <ModelRow
                          key={m.id}
                          model={m}
                          active={m.id === selectedModel}
                          disabled={m.disabled}
                          onClick={() => !m.disabled && pickModel(m.id)}
                          compact
                        />
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>,
          document.body
        )}
    </>
  );
}

function FlyoutRow({
  label,
  value,
  open,
  onOpen,
}: {
  label: string;
  value?: string;
  open: boolean;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      onMouseEnter={onOpen}
      className={`btn-icon flex w-full items-center justify-between rounded-xl px-3 py-2.5 text-left transition-colors ${
        open ? "bg-(--card-hover)" : "hover:bg-(--card-hover)"
      }`}
    >
      <span className="text-[13.5px] font-medium text-foreground/90">{label}</span>
      <span className="flex items-center gap-1 text-[12.5px] text-(--muted)">
        {value}
        <ChevronRight className="h-4 w-4" />
      </span>
    </button>
  );
}

function ModelRow({
  model,
  active,
  disabled,
  onClick,
  onHover,
  compact,
}: {
  model: ModelOption;
  active: boolean;
  disabled?: boolean;
  onClick: () => void;
  onHover?: () => void;
  compact?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={onHover}
      disabled={disabled}
      className={`btn-icon flex w-full items-center justify-between rounded-xl px-3 text-left transition-colors ${
        compact ? "py-2" : "py-2.5"
      } ${active ? "bg-(--card-hover)" : "hover:bg-(--card-hover)"} ${disabled ? "cursor-not-allowed opacity-40" : ""}`}
    >
      <div>
        <div className={`text-[13.5px] font-medium ${active ? "text-foreground" : "text-foreground/85"}`}>
          {model.label}
        </div>
        {!compact && <div className="text-[11.5px] text-(--muted)">{model.description}</div>}
      </div>
      {active && <Check className="ml-2 h-4 w-4 shrink-0 text-blue-500" strokeWidth={2.5} />}
    </button>
  );
}

/** Clean pill switch — explicit pixel dims so global button CSS can't distort it. */
function Toggle({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      onClick={onClick}
      className="btn-icon relative shrink-0 cursor-pointer rounded-full transition-colors"
      style={{
        width: 38,
        height: 22,
        padding: 0,
        border: "none",
        background: on ? "#2563eb" : "#52525b",
      }}
    >
      <span
        className="absolute rounded-full bg-white transition-all"
        style={{ width: 16, height: 16, top: 3, left: on ? 19 : 3 }}
      />
    </button>
  );
}
