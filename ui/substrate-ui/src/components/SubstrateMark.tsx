import type { SVGProps } from "react";

/**
 * The product mark, shown in the sidebar and empty states. The default is the platform's two-square logo, so a chatbot deployed from the
 * platform looks like it. To use your own, set `NEXT_PUBLIC_BRAND_LOGO_URL` (any image URL) and it replaces this everywhere.
 */
export function SubstrateMark({ className, ...props }: SVGProps<SVGSVGElement>) {
  const custom = process.env.NEXT_PUBLIC_BRAND_LOGO_URL;
  if (custom) {
    // eslint-disable-next-line @next/next/no-img-element -- a deployer-supplied URL of unknown origin and size
    return <img src={custom} alt="" aria-hidden="true" className={`object-contain ${className ?? ""}`} />;
  }
  return (
    <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" className={className} {...props}>
      <rect x="2" y="2" width="13" height="13" rx="2.5" fill="var(--accent)" transform="rotate(8 8 8)" />
      <rect x="9" y="9" width="13" height="13" rx="2.5" fill="var(--accent-2)" transform="rotate(8 15 15)" />
    </svg>
  );
}
