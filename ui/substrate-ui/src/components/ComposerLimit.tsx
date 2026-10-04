"use client";

import { Tooltip, TooltipProvider } from "@/design";
import { useRateLimit } from "@/hooks/useRateLimit";
import { formatResetIn, limitTone } from "@/lib/limits";

const STROKE = { success: "var(--success)", warning: "var(--warning)", danger: "var(--danger)" } as const;
const SIZE = 22;
const WIDTH = 2.5;

/**
 * The day's message allowance as a small ring beside the model picker: it fills as the day's messages are used (green, amber from 75%, red when reached).
 * Hovering or focusing it says how many are left and when it resets. Nothing is shown when no limit applies.
 */
export function ComposerLimit({ refreshOn }: { refreshOn?: unknown }) {
  const status = useRateLimit(refreshOn);
  if (!status) return null;
  const { used, limit, reset_in } = status;
  const left = Math.max(0, limit - used);
  const fraction = Math.min(1, used / Math.max(1, limit));
  const radius = (SIZE - WIDTH) / 2;
  const circumference = 2 * Math.PI * radius;
  const label = left === 0 ? `Daily limit reached · resets in ${formatResetIn(reset_in)}` : `${left} of ${limit} messages left today · resets in ${formatResetIn(reset_in)}`;

  return (
    <TooltipProvider>
      <Tooltip label={label}>
        <span tabIndex={0} role="img" aria-label={label} className="flex size-9 shrink-0 cursor-default items-center justify-center rounded-full outline-none focus-visible:ring-1 focus-visible:ring-accent">
          <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`} className="-rotate-90">
            <circle cx={SIZE / 2} cy={SIZE / 2} r={radius} fill="none" stroke="var(--border)" strokeWidth={WIDTH} />
            <circle
              cx={SIZE / 2}
              cy={SIZE / 2}
              r={radius}
              fill="none"
              stroke={STROKE[limitTone(used, limit)]}
              strokeWidth={WIDTH}
              strokeLinecap="round"
              strokeDasharray={circumference}
              strokeDashoffset={circumference * (1 - fraction)}
              className="transition-[stroke-dashoffset] duration-500"
            />
          </svg>
        </span>
      </Tooltip>
    </TooltipProvider>
  );
}
