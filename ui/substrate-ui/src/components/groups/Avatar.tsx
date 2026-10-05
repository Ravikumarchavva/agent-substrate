import { cn } from "@/design";
import { hueOf, initialOf } from "./text";

/** A round initial in the person's own colour. */
export function Avatar({ name, className }: { name: string; className?: string }) {
  const hue = hueOf(name);
  return (
    <span
      aria-hidden
      className={cn("flex size-9 shrink-0 items-center justify-center rounded-full text-sm font-semibold", className)}
      style={{ backgroundColor: `hsl(${hue} 60% 55% / 0.18)`, color: `hsl(${hue} 55% 45%)` }}
    >
      {initialOf(name)}
    </span>
  );
}
