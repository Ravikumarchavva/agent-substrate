import type { LucideIcon } from "lucide-react";
import { cn } from "../cn";

/** A ring showing how much of a limit is left: full when untouched, shrinking as it is spent (green, then amber, then red). */
export function Meter({ icon: Icon, label, used, limit, className }: { icon: LucideIcon; label: string; used: number; limit: number; className?: string }) {
  const spent = limit > 0 ? Math.min(100, (used / limit) * 100) : 0;
  const tone = spent >= 100 ? "text-danger" : spent >= 75 ? "text-warning" : "text-success";
  const circumference = 2 * Math.PI * 14;
  return (
    <div
      role="img"
      aria-label={`${label}: ${used} of ${limit} used`}
      title={`${label}: ${Math.max(0, limit - used)} of ${limit} left`}
      className={cn("relative flex size-control-md shrink-0 items-center justify-center text-muted", className)}
    >
      <svg className="absolute inset-0 -rotate-90" viewBox="0 0 32 32" aria-hidden="true">
        <circle cx="16" cy="16" r="14" fill="none" strokeWidth="2" className="stroke-border" />
        <circle
          cx="16"
          cy="16"
          r="14"
          fill="none"
          strokeWidth="2"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (spent / 100)}
          className={cn("stroke-current transition-all duration-500", tone)}
        />
      </svg>
      <Icon className="size-3.5" />
    </div>
  );
}
