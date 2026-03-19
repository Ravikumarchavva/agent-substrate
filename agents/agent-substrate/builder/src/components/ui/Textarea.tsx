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
        "flex field-sizing-content min-h-[72px] w-full rounded-lg border border-(--bg-hover) bg-(--bg-elevated) px-3 py-2.5 text-[13px] font-normal text-(--text) leading-relaxed transition-colors outline-none placeholder:text-(--text-dim) focus-visible:border-(--accent) focus-visible:ring-2 focus-visible:ring-(--accent)/25 disabled:cursor-not-allowed disabled:opacity-40 resize-none",
        className
      )}
      {...props}
    />
  )
}

export { Textarea }
