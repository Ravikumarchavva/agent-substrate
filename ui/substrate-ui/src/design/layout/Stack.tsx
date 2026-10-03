import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";
import { cn } from "../cn";

/** Spacing between siblings is the parent's job: children set no margins. */
const stackVariants = cva("flex", {
  variants: {
    direction: { column: "flex-col", row: "flex-row flex-wrap items-center" },
    gap: { xs: "gap-1", sm: "gap-2", md: "gap-4", lg: "gap-6", xl: "gap-8" },
  },
  defaultVariants: { direction: "column", gap: "md" },
});

export function Stack({ className, direction, gap, ...props }: HTMLAttributes<HTMLDivElement> & VariantProps<typeof stackVariants>) {
  return <div className={cn(stackVariants({ direction, gap }), className)} {...props} />;
}
