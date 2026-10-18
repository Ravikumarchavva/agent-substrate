"use client";

/**
 * Small SVG progress ring for the composer's per-attachment upload status —
 * thin outline at 0%, fully filled at 100%. `progress` is simulated
 * (elapsed-time vs. an expected-time-per-page estimate) rather than real
 * ground truth: the extraction pipeline batches internally and can't report
 * true per-page progress (see useFileAttachments.ts). `state` overrides the
 * ring's color for the terminal states.
 */

interface CircularProgressProps {
  progress: number; // 0-1
  state: "pending" | "ready" | "error";
  size?: number;
}

export function CircularProgress({ progress, state, size = 20 }: CircularProgressProps) {
  const strokeWidth = 2;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const clamped = Math.max(0, Math.min(1, progress));
  const offset = circumference * (1 - clamped);

  // Matches this project's existing convention of hardcoded Tailwind
  // red/green hex values for error/success states (see RateLimitBar.tsx)
  // rather than a new CSS custom property.
  const color =
    state === "error" ? "#ef4444" : state === "ready" ? "#22c55e" : "var(--accent)";

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      className="shrink-0"
      role="img"
      aria-label={
        state === "error" ? "Processing failed" : state === "ready" ? "Ready" : "Processing"
      }
    >
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="var(--border)"
        strokeWidth={strokeWidth}
      />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        strokeDasharray={circumference}
        strokeDashoffset={state === "ready" ? 0 : offset}
        strokeLinecap="round"
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
        style={{ transition: "stroke-dashoffset 0.3s ease" }}
      />
    </svg>
  );
}
