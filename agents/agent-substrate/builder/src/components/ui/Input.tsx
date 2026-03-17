import * as React from "react"
import { Input as InputPrimitive } from "@base-ui/react/input"

import { cn } from "@/lib/utils"

interface InputProps extends Omit<React.ComponentProps<"input">, "onChange"> {
  value: string | number
  onChange: (value: string) => void
}

function Input({ className, type, onChange, ...props }: InputProps) {
  return (
    <InputPrimitive
      type={type}
      data-slot="input"
      onChange={(event) => onChange(event.target.value)}
      className={cn(
        "h-7 w-full min-w-0 rounded-md border border-(--border) bg-(--bg) px-2 py-1 text-xs text-(--text) transition-colors outline-none placeholder:text-(--text-dim) focus-visible:border-(--accent) focus-visible:ring-1 focus-visible:ring-(--accent)/40 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-40",
        className
      )}
      {...props}
    />
  )
}

export { Input }
