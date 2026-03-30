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
};

export function HumanInputCard({
  requestId,
  question,
  context,
  options,
  allowFreeform = true,
  onRespond,
}: HumanInputCardProps) {
  const [answered, setAnswered] = useState(false);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [freeformText, setFreeformText] = useState("");
  const freeformRef = useRef<HTMLInputElement>(null);

  // When user clicks freeform row, focus the input
  useEffect(() => {
    if (selectedKey === "__freeform__") {
      freeformRef.current?.focus();
    }
  }, [selectedKey]);

  function submit(key: string, label: string, freeform?: string) {
    setAnswered(true);
    if (freeform !== undefined) {
      onRespond(requestId, { freeform_text: freeform });
    } else {
      onRespond(requestId, { selected_key: key, selected_label: label });
    }
  }

  function handleRowClick(opt: Option) {
    if (answered) return;
    setSelectedKey(opt.key);
    // Immediate submit for predefined options
    submit(opt.key, opt.label);
  }

  function handleFreeformRowClick() {
    if (answered) return;
    setSelectedKey("__freeform__");
  }

  function handleFreeformSubmit() {
    if (!freeformText.trim() || answered) return;
    submit("__freeform__", freeformText.trim(), freeformText.trim());
  }

  const answeredLabel =
    selectedKey === "__freeform__"
      ? freeformText
      : options.find((o) => o.key === selectedKey)?.label ?? "";

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

        {/* Answered state */}
        {answered ? (
          <div className="px-4 py-3 flex items-center gap-2">
            <div
              className="w-5 h-5 rounded-full flex items-center justify-center shrink-0"
              style={{ background: "var(--accent)" }}
            >
              <Check className="w-3 h-3 text-white" strokeWidth={3} />
            </div>
            <span className="text-xs" style={{ color: "var(--muted)" }}>
              Answered:{" "}
              <span className="font-medium" style={{ color: "var(--foreground)" }}>
                {answeredLabel}
              </span>
            </span>
          </div>
        ) : (
          <div className="py-1">
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
                      color:  isSelected ? "#fff" : "var(--muted)",
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
                    color: selectedKey === "__freeform__" ? "#fff" : "var(--muted)",
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
                    className="shrink-0 text-[11px] font-semibold px-2 py-0.5 rounded cursor-pointer text-white"
                    style={{ background: "var(--accent)" }}
                  >
                  Send
                  </button>
                )}
              </div>
            )}
          </div>
        )}
    </div>
  );
}

