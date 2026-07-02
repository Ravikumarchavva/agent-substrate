"use client";

import { useState, useRef, useEffect } from "react";
import { Check } from "lucide-react";

type Option = {
  key: string;
  label: string;
  description?: string;
};

type HumanInputCardProps = {
  requestId: string;
  question: string;
  context?: string;
  options: Option[];
  allowFreeform?: boolean;
  onRespond: (requestId: string, data: Record<string, unknown>) => void;
  /** When set, the card mounts in a read-only, already-answered state (history reload). */
  initialStatus?: "answered" | "skipped";
  /** The answer label to show when initialStatus === "answered". */
  initialAnswerLabel?: string;
};

export function HumanInputCard({
  requestId,
  question,
  context,
  options,
  allowFreeform = true,
  onRespond,
  initialStatus,
  initialAnswerLabel,
}: HumanInputCardProps) {
  const isHistory = initialStatus !== undefined;
  const [answered, setAnswered] = useState(initialStatus === "answered");
  const [skipped, setSkipped] = useState(initialStatus === "skipped");
  const [collapsed, setCollapsed] = useState(isHistory);
  const [selectedKey, setSelectedKey] = useState<string | null>(
    initialStatus === "answered"
      ? options.find((o) => o.label === initialAnswerLabel)?.key ?? "__freeform__"
      : null
  );
  const [freeformText, setFreeformText] = useState(
    initialStatus === "answered" && !options.some((o) => o.label === initialAnswerLabel)
      ? initialAnswerLabel ?? ""
      : ""
  );
  const freeformRef = useRef<HTMLInputElement>(null);

  // When user clicks freeform row, focus the input (skip in read-only history mode)
  useEffect(() => {
    if (!isHistory && selectedKey === "__freeform__") {
      freeformRef.current?.focus();
    }
  }, [selectedKey, isHistory]);

  function submit(key: string, label: string, freeform?: string) {
    setAnswered(true);
    setCollapsed(true);
    if (freeform !== undefined) {
      onRespond(requestId, { action: "answered", freeform_text: freeform });
    } else {
      onRespond(requestId, { action: "answered", selected_key: key, selected_label: label });
    }
  }

  function handleSkip() {
    if (answered || skipped) return;
    setSkipped(true);
    setCollapsed(true);
    onRespond(requestId, { action: "skipped" });
  }

  function handleRowClick(opt: Option) {
    if (answered || skipped) return;
    setSelectedKey(opt.key);
    // Immediate submit for predefined options
    submit(opt.key, opt.label);
  }

  function handleFreeformRowClick() {
    if (answered || skipped) return;
    setSelectedKey("__freeform__");
  }

  function handleFreeformSubmit() {
    if (!freeformText.trim() || answered || skipped) return;
    submit("__freeform__", freeformText.trim(), freeformText.trim());
  }

  const answeredLabel =
    selectedKey === "__freeform__"
      ? freeformText
      : options.find((o) => o.key === selectedKey)?.label ?? "";

  const answeredOptionKey = selectedKey !== "__freeform__" ? selectedKey : null;

  return (
    <div
      className="w-full text-sm overflow-hidden"
      style={{
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: "10px",
      }}
    >
        {/* Header */}
        <div
          className="px-4 pt-3 pb-2"
          style={{ borderBottom: "1px solid var(--border)" }}
        >
          {context && (
            <p className="text-xs mb-1" style={{ color: "var(--muted)" }}>
              {context}
            </p>
          )}
          <p className="font-semibold text-sm leading-snug" style={{ color: "var(--foreground)" }}>
            {question}
          </p>
        </div>

        {/* Answered / skipped state */}
        {(answered || skipped) ? (
          <>
            {/* Compact confirmed row — click to expand */}
            <button
              onClick={() => setCollapsed((c) => !c)}
              className="w-full px-4 py-2.5 flex items-center gap-2 text-left cursor-pointer transition-colors"
              onMouseEnter={(e) => { e.currentTarget.style.background = "var(--card-hover)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = "transparent"; }}
            >
              <div
                className="w-4 h-4 rounded-full flex items-center justify-center shrink-0"
                style={{ background: skipped ? "color-mix(in srgb, var(--muted) 40%, transparent)" : "var(--accent)" }}
              >
                <Check className="w-2.5 h-2.5" style={{ color: skipped ? "var(--muted)" : "var(--accent-foreground)" }} strokeWidth={3} />
              </div>
              <span className="text-xs flex-1 min-w-0" style={{ color: "var(--muted)" }}>
                {skipped ? (
                  "Skipped"
                ) : (
                  <>
                    <span style={{ color: "var(--muted)" }}>Answered: </span>
                    <span className="font-medium" style={{ color: "var(--foreground)" }}>
                      {answeredLabel}
                    </span>
                  </>
                )}
              </span>
              <span className="text-[10px] shrink-0" style={{ color: "var(--muted)" }}>
                {collapsed ? "▸" : "▾"}
              </span>
            </button>

            {/* Expanded read-only view */}
            {!collapsed && (
              <div className="py-1 opacity-60 pointer-events-none" style={{ borderTop: "1px solid var(--border)" }}>
                {options.map((opt, idx) => {
                  const isSelected = opt.key === answeredOptionKey;
                  return (
                    <div key={opt.key} className="flex items-center gap-3 px-4 py-2">
                      <span
                        className="shrink-0 w-5 h-5 flex items-center justify-center text-[11px] font-semibold rounded-sm"
                        style={{
                          background: isSelected ? "var(--accent)" : "var(--step-bg)",
                          color: isSelected ? "var(--accent-foreground)" : "var(--muted)",
                          border: isSelected ? "none" : "1px solid var(--border)",
                        }}
                      >
                        {idx + 1}
                      </span>
                      <span className="text-xs" style={{ color: isSelected ? "var(--accent)" : "var(--foreground)" }}>
                        {opt.label}
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </>
        ) : (
          <div className="py-1">
            {/* Fallback: no options and no freeform — show simple acknowledge button */}
            {options.length === 0 && !allowFreeform && (
              <div className="px-4 py-3">
                <button
                  onClick={() => submit("__ack__", "Acknowledged")}
                  className="w-full rounded-lg px-4 py-2 text-xs font-semibold cursor-pointer transition-colors"
                  style={{ background: "var(--accent)", color: "var(--accent-foreground)" }}
                >
                  Acknowledged
                </button>
              </div>
            )}
            {/* Predefined options */}
            {options.map((opt, idx) => {
              const isSelected = selectedKey === opt.key;
              return (
                <button
                  key={opt.key}
                  onClick={() => handleRowClick(opt)}
                  className="w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors cursor-pointer"
                  style={{
                    background: isSelected
                      ? "color-mix(in srgb, var(--accent) 12%, transparent)"
                      : "transparent",
                    borderLeft: isSelected
                      ? "2px solid var(--accent)"
                      : "2px solid transparent",
                  }}
                  onMouseEnter={(e) => {
                    if (!isSelected) e.currentTarget.style.background = "var(--card-hover)";
                  }}
                  onMouseLeave={(e) => {
                    if (!isSelected) e.currentTarget.style.background = "transparent";
                  }}
                >
                  {/* Number badge */}
                  <span
                    className="shrink-0 w-5 h-5 flex items-center justify-center text-[11px] font-semibold rounded-sm"
                    style={{
                      background: isSelected ? "var(--accent)" : "var(--step-bg)",
                      color:  isSelected ? "var(--accent-foreground)" : "var(--muted)",
                      border: isSelected ? "none" : "1px solid var(--border)",
                    }}
                  >
                    {idx + 1}
                  </span>

                  {/* Label + description */}
                  <span className="flex-1 min-w-0 text-xs">
                    <span
                      className="font-semibold"
                      style={{ color: isSelected ? "var(--accent)" : "var(--foreground)" }}
                    >
                      {opt.label}
                    </span>
                    {opt.description && (
                      <span style={{ color: "var(--muted)" }}>
                        : <span className="truncate">{opt.description}</span>
                      </span>
                    )}
                  </span>

                  {/* Checkmark */}
                  {isSelected && (
                    <Check
                      className="shrink-0 w-3.5 h-3.5"
                      style={{ color: "var(--accent)" }}
                      strokeWidth={3}
                    />
                  )}
                </button>
              );
            })}

            {/* Freeform row */}
            {allowFreeform && (
              <div
                className="flex items-center gap-3 px-4 py-2.5 cursor-text transition-colors"
                onClick={handleFreeformRowClick}
                style={{
                  background:
                    selectedKey === "__freeform__"
                      ? "color-mix(in srgb, var(--accent) 12%, transparent)"
                      : "transparent",
                  borderLeft:
                    selectedKey === "__freeform__"
                      ? "2px solid var(--accent)"
                      : "2px solid transparent",
                }}
                onMouseEnter={(e) => {
                  if (selectedKey !== "__freeform__")
                    (e.currentTarget as HTMLElement).style.background = "var(--card-hover)";
                }}
                onMouseLeave={(e) => {
                  if (selectedKey !== "__freeform__")
                    (e.currentTarget as HTMLElement).style.background = "transparent";
                }}
              >
                {/* Number badge */}
                <span
                  className="shrink-0 w-5 h-5 flex items-center justify-center text-[11px] font-semibold rounded-sm"
                  style={{
                    background:
                      selectedKey === "__freeform__" ? "var(--accent)" : "var(--step-bg)",
                    color: selectedKey === "__freeform__" ? "var(--accent-foreground)" : "var(--muted)",
                    border:
                      selectedKey === "__freeform__" ? "none" : "1px solid var(--border)",
                  }}
                >
                  {options.length + 1}
                </span>

                {/* Input */}
                <input
                  ref={freeformRef}
                  type="text"
                  value={freeformText}
                  onChange={(e) => setFreeformText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") handleFreeformSubmit();
                    e.stopPropagation();
                  }}
                  onClick={(e) => {
                    e.stopPropagation();
                    handleFreeformRowClick();
                  }}
                  placeholder="Enter custom answer"
                  className="flex-1 bg-transparent outline-none text-xs"
                  style={{ color: "var(--foreground)" }}
                />

                {selectedKey === "__freeform__" && freeformText.trim() && (
                  <button
                    onClick={(e) => { e.stopPropagation(); handleFreeformSubmit(); }}
                    className="shrink-0 text-[11px] font-semibold px-2 py-0.5 rounded cursor-pointer text-(--accent-foreground)"
                    style={{ background: "var(--accent)" }}
                  >
                  Send
                  </button>
                )}
              </div>
            )}

            {/* Skip — always shown at bottom */}
            <div style={{ borderTop: "1px solid var(--border)" }} className="px-4 py-2">
              <button
                onClick={handleSkip}
                className="text-xs cursor-pointer transition-colors"
                style={{ color: "var(--muted)" }}
                onMouseEnter={(e) => { e.currentTarget.style.color = "var(--foreground)"; }}
                onMouseLeave={(e) => { e.currentTarget.style.color = "var(--muted)"; }}
              >
                Skip
              </button>
            </div>
          </div>
        )}
    </div>
  );
}

