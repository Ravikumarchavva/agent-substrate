import * as React from "react"

import { cn } from "@/lib/utils"

interface TextareaProps extends Omit<React.ComponentProps<"textarea">, "onChange"> {
  value: string
  onChange: (value: string) => void
}

function Textarea({ className, onChange, ...props }: TextareaProps) {
  return (
    <textarea
      data-slot="textarea"
      onChange={(event) => onChange(event.target.value)}
      className={cn(
        "flex field-sizing-content min-h-12 w-full rounded-md border border-(--border) bg-(--bg) px-2 py-1.5 text-xs text-(--text) leading-relaxed transition-colors outline-none placeholder:text-(--text-dim) focus-visible:border-(--accent) focus-visible:ring-1 focus-visible:ring-(--accent)/40 disabled:cursor-not-allowed disabled:opacity-40",
        className
      )}
      {...props}
    />
  )
}

export { Textarea }
