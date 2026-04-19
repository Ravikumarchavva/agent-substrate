import type { SVGProps } from "react";

type SidebarToggleDirection = "open" | "close";

interface SidebarToggleIconProps extends SVGProps<SVGSVGElement> {
  direction: SidebarToggleDirection;
}

export function SidebarToggleIcon({ direction, ...props }: SidebarToggleIconProps) {
  const dividerX = direction === "open" ? "7.1" : "12.9";

  return (
    <svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" {...props}>
      <rect x="2.5" y="3" width="15" height="14" rx="3" stroke="currentColor" strokeWidth="1.6" />
      <path d={`M${dividerX} 3.8V16.2`} stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}