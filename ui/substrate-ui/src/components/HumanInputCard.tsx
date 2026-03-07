"use client";

import { useState } from "react";
import { MessageCircleQuestion } from "lucide-react";
import { PanelShell } from "@/components/PanelShell";

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
  const [selectedAnswer, setSelectedAnswer] = useState("");
  const [freeformText, setFreeformText] = useState("");
  const [showFreeform, setShowFreeform] = useState(false);

  function handleSelectOption(opt: Option) {
    setAnswered(true);
    setSelectedAnswer(opt.label);
    onRespond(requestId, {
      selected_key: opt.key,
      selected_label: opt.label,
    });
  }

  function handleFreeformSubmit() {
    if (!freeformText.trim()) return;
    setAnswered(true);
    setSelectedAnswer(freeformText.trim());
    onRespond(requestId, {
      freeform_text: freeformText.trim(),
    });
  }

  return (
    <div className="flex justify-start">
      <div className="max-w-[85%] w-full text-sm">
        <PanelShell
          icon={<MessageCircleQuestion className="w-4 h-4" style={{ color: "var(--accent)" }} />}
          title="Input Needed"
          collapsible={false}
        >

        {context && (
          <p className="mb-2 text-xs" style={{ color: "var(--muted)" }}>{context}</p>
        )}

        <p className="mb-3 text-sm" style={{ color: "var(--foreground)" }}>{question}</p>

        {/* Answered state */}
        {answered ? (
          <div
            className="inline-block rounded px-2 py-1 text-xs font-medium"
            style={{ background: "color-mix(in srgb, var(--accent) 15%, transparent)", color: "var(--accent)" }}
          >
            Answered: {selectedAnswer}
          </div>
        ) : (
          <div className="space-y-2">
            {/* Option buttons */}
            <div className="flex flex-wrap gap-2">
              {options.map((opt) => (
                <button
                  key={opt.key}
                  onClick={() => handleSelectOption(opt)}
                  className="rounded-md px-3 py-2 text-left text-xs transition-colors"
                  style={{
                    background: "var(--card)",
                    border: "1px solid var(--border)",
                    color: "var(--foreground)",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.borderColor = "var(--accent)";
                    e.currentTarget.style.background = "var(--card-hover)";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.borderColor = "var(--border)";
                    e.currentTarget.style.background = "var(--card)";
                  }}
                >
                  <span className="font-medium">{opt.label}</span>
                  {opt.description && (
                    <span className="ml-1" style={{ color: "var(--muted)" }}>— {opt.description}</span>
                  )}
                </button>
              ))}
            </div>

            {/* Freeform input */}
            {allowFreeform && (
              <div>
                {showFreeform ? (
                  <div className="flex gap-2">
                    <input
                      type="text"
                      value={freeformText}
                      onChange={(e) => setFreeformText(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") handleFreeformSubmit();
                      }}
                      placeholder="Type your answer..."
                      className="flex-1 rounded px-2 py-1.5 text-xs outline-none"
                      style={{
                        background: "var(--code-bg)",
                        color: "var(--foreground)",
                        border: "1px solid var(--border)",
                      }}
                      onFocus={(e) => (e.currentTarget.style.borderColor = "var(--accent)")}
                      onBlur={(e) => (e.currentTarget.style.borderColor = "var(--border)")}
                      autoFocus
                    />
                    <button
                      onClick={handleFreeformSubmit}
                      disabled={!freeformText.trim()}
                      className="rounded px-3 py-1.5 text-xs font-medium text-white transition-colors disabled:opacity-50"
                      style={{ background: "var(--accent)" }}
                    >
                      Send
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => setShowFreeform(true)}
                    className="text-xs transition-colors"
                    style={{ color: "var(--muted)" }}
                    onMouseEnter={(e) => (e.currentTarget.style.color = "var(--foreground)")}
                    onMouseLeave={(e) => (e.currentTarget.style.color = "var(--muted)")}
                  >
                    Other — type your own answer
                  </button>
                )}
              </div>
            )}
          </div>
        )}
        </PanelShell>
      </div>
    </div>
  );
}
