import { cn } from "@/design";
import { buildObjectUrl } from "@/lib/api/_client";
import { hueOf, initialOf } from "./text";

/** A person's picture, or their initial in their own colour when they have none. `src` is the stored picture's key. */
export function Avatar({ name, src, className }: { name: string; src?: string | null; className?: string }) {
  if (src) {
    // eslint-disable-next-line @next/next/no-img-element -- an authenticated, already-resized 256px picture: nothing for next/image to optimise
    return <img src={buildObjectUrl(src)} alt="" aria-hidden className={cn("size-9 shrink-0 rounded-full object-cover", className)} />;
  }
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
