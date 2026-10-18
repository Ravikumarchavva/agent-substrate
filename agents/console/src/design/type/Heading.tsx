import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";
import { cn } from "../cn";

const headingVariants = cva("font-semibold tracking-tight text-foreground text-balance", {
  variants: {
    level: { page: "text-3xl", section: "text-2xl", title: "text-lg", label: "text-sm" },
  },
  defaultVariants: { level: "section" },
});

const TAGS = { page: "h1", section: "h2", title: "h3", label: "h4" } as const;

/** The only headings: `page` > `section` > `title` > `label`. The tag follows the level; pass `as` to override. */
export function Heading({ level = "section", as, className, ...props }: HTMLAttributes<HTMLHeadingElement> & VariantProps<typeof headingVariants> & { as?: "h1" | "h2" | "h3" | "h4" | "p" }) {
  const Tag = as ?? TAGS[level ?? "section"];
  return <Tag className={cn(headingVariants({ level }), className)} {...props} />;
}
