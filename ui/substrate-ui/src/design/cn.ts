import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge class names; the last Tailwind utility wins, so a caller's `className` can override a component's default. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
