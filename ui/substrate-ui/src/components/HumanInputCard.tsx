"use client";

import { useState } from "react";

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
      <div className="max-w-[85%] rounded-lg border border-blue-600/40 bg-zinc-900 p-4 text-sm">
        {/* Header */}
        <div className="mb-2 flex items-center gap-2">
          <span className="rounded bg-blue-600/20 px-2 py-0.5 text-xs font-semibold text-blue-400">
            🤔 Input Needed
          </span>
        </div>

        {context && (
          <p className="mb-2 text-xs text-zinc-400">{context}</p>
        )}

        <p className="mb-3 text-zinc-100">{question}</p>

        {/* Answered state */}
        {answered ? (
          <div className="inline-block rounded bg-blue-600/20 px-2 py-1 text-xs font-medium text-blue-400">
            ✅ Answered: {selectedAnswer}
          </div>
        ) : (
          <div className="space-y-2">
            {/* Option buttons */}
            <div className="flex flex-wrap gap-2">
              {options.map((opt) => (
                <button
                  key={opt.key}
                  onClick={() => handleSelectOption(opt)}
                  className="group rounded-md border border-zinc-700 bg-zinc-800 px-3 py-2 text-left text-xs transition-colors hover:border-blue-500 hover:bg-zinc-700"
                >
                  <span className="font-medium text-zinc-200">
                    {opt.label}
                  </span>
                  {opt.description && (
                    <span className="ml-1 text-zinc-400">
                      — {opt.description}
                    </span>
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
                      className="flex-1 rounded bg-zinc-800 px-2 py-1.5 text-xs text-zinc-200 outline-none focus:ring-1 focus:ring-blue-500"
                      autoFocus
                    />
                    <button
                      onClick={handleFreeformSubmit}
                      disabled={!freeformText.trim()}
                      className="rounded bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-500 transition-colors disabled:opacity-50"
                    >
                      Send
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => setShowFreeform(true)}
                    className="text-xs text-zinc-400 hover:text-zinc-200 transition-colors"
                  >
                    ✏️ Other — type your own answer
                  </button>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
