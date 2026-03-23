---
name: "TypeScript Type Conventions"
description: "Shared type location, discriminated unions for SSE events, and strictness rules"
applyTo: "src/types/**"
---

# TypeScript Type Conventions

## Shared types location
All shared types live in `src/types/index.ts`. Never duplicate a type locally in a component.

## Current types
```ts
Thread       { id, name, created_at }
Message      { id, role, content, tool_calls?, metadata? }
ToolCall     { id, name, input, result? }
BackendMessage { role, content, ... }
TaskStatus   "todo" | "in_progress" | "done"
Task         { id, title, status, created_at }
TaskList     { id, conversation_id, tasks: Task[] }
```

## Adding a new type
1. Add it to `src/types/index.ts` with a JSDoc comment explaining its purpose.
2. Keep union types narrow — avoid `string` when a specific string literal union is possible.
3. Export all types as named exports from `index.ts`.

## Discriminated unions for SSE events
When typing SSE event payloads, use a `type` discriminator:
```ts
type SSEEvent =
  | { type: "text_delta";          content: string; partial: boolean }
  | { type: "task_list_created";   task_list: TaskList }
  | { type: "tool_approval_request"; requestId: string; tool_name: string; input: unknown }
```

## Rules
- No `any` — use `unknown` + type guards for truly dynamic data.
- Prefer `interface` for object shapes, `type` for unions and aliases.
- Import with `import type` when only using a type at compile time (no runtime value needed).
