import type { SVGProps } from "react";

/**
 * The official Substrate product mark (two overlapping rounded squares),
 * matching substrate-ui branding.
 */
export function SubstrateMark({
  className,
  ...props
}: SVGProps<SVGSVGElement>) {
  return (
    <svg
      data-icon
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      className={className}
      {...props}
    >
      <rect
        x="2"
        y="2"
        width="13"
        height="13"
        rx="2.5"
        fill="var(--violet)"
        transform="rotate(8 8 8)"
      />
      <rect
        x="9"
        y="9"
        width="13"
        height="13"
        rx="2.5"
        fill="var(--orange)"
        transform="rotate(8 15 15)"
      />
    </svg>
  );
}
