---
applyTo: "src/components/**"
description: Rules for authoring React components
---

# Component Authoring Rules

## File structure
```tsx
"use client"   // ← only if component uses hooks / browser APIs

import { ... } from "react"
import { ... } from "@/types"
// ... other imports

interface MyComponentProps {
  // explicit typed props — never inline complicated types in the function signature
}

export function MyComponent({ prop1, prop2 }: MyComponentProps) {
  // hooks at the top
  // local state
  // event handlers
  // render
  return (...)
}
```

## Naming
- Component file = `PascalCase.tsx`
- Component name = same as file name (named export, not default)
- Handlers = `handleActionNoun` (e.g. `handleTaskDelete`, `handleSendMessage`)
- Boolean props = `is*` or `on*`, e.g. `isLoading`, `onDismiss`

## Styling — Tailwind v4 rules (IMPORTANT)
Always use the v4 variable shorthand:
- `border-(--border)` NOT `border-[var(--border)]`
- `bg-(--card)` NOT `bg-[var(--card)]`
- `hover:bg-(--card-hover)` NOT `hover:bg-[var(--card-hover)]`
- `bg-background` for `--background`, `text-foreground` for `--foreground`
- `bg-linear-to-br` NOT `bg-gradient-to-br` for gradients

## TypeScript strictness
- No `any`. Use `unknown` + type narrowing if shape is unclear.
- Import shared types from `@/types` — don't redefine them locally.
- All event handler params must be typed (e.g. `e: React.ChangeEvent<HTMLInputElement>`).

## Component boundaries
| Component | Responsibility |
|---|---|
| `Header` | Top nav, theme toggle, settings |
| `Sidebar` | Thread list, new chat |
| `MessageBubble` | Renders a single chat turn (text + tool calls) |
| `AppPanel` | Right panel — hosts MCP App UI iframes / widgets |
| `KanbanPanel` | Kanban board; **purely presentational** — no API calls |
| `HumanInputCard` | HITL text input prompt |
| `ToolApprovalCard` | HITL tool approval buttons |

## State lifting
- Global app state (threads, messages, taskList, theme) lives in `page.tsx`.
- Components receive data and emit callbacks upward.
- As the project grows, migrate to `useContext` before reaching for Zustand or Redux.

## Accessibility
- Interactive elements must have `aria-label` or visible text.
- Use semantic HTML: `<button>`, `<nav>`, `<aside>`, `<header>`, `<main>`.
- Keyboard navigation: `onKeyDown` handlers for Enter/Space on custom interactive elements.
- **Cursor**: Every `<button>`, `<a>`, and clickable `<div>` MUST include `cursor-pointer` in its `className`. Never rely on the browser default — always be explicit.
