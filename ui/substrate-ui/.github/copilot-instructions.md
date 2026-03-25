# AI Chatbot UI — GitHub Copilot Instructions

## Project Overview
Next.js 16 (App Router) frontend for the agent-framework backend.
Streams chat responses via SSE, renders interactive MCP App widgets,
shows a live Kanban task board, and supports HITL (human-in-the-loop) approvals.

---

## Stack
| Concern | Choice |
|---|---|
| Framework | **Next.js 16** with App Router |
| Language | **TypeScript** (strict) |
| Styling | **Tailwind CSS v4** |
| Package mgr | **pnpm** |
| State | React `useState` / `useReducer` (no external store) |
| API comms | `fetch` + `EventSource` (SSE) |
| Backend | `http://localhost:8000` (env: `NEXT_PUBLIC_API_URL`) |

---

## Key Files & Directories
```
src/
├── app/
│   ├── page.tsx              ← Main chat page (state, SSE loop, layout)
│   ├── layout.tsx            ← Root layout + ThemeProvider
│   ├── globals.css           ← CSS custom properties (--background, --card, etc.)
│   └── api/
│       ├── chat/route.ts     ← Proxy to backend /chat SSE
│       ├── chat/respond/[requestId]/route.ts ← HITL approval bridge
│       └── logs/route.ts     ← Frontend log collector for Loki
├── components/
│   ├── Header.tsx            ← Top bar, theme toggle, settings menu
│   ├── Sidebar.tsx           ← Thread list, new chat button
│   ├── MessageBubble.tsx     ← Chat message rendering (markdown, tool calls)
│   ├── AppPanel.tsx          ← Right panel for MCP App UI widgets
│   ├── KanbanPanel.tsx       ← Live Kanban board driven by agent task events
│   ├── HumanInputCard.tsx    ← HITL input prompt card
│   └── ToolApprovalCard.tsx  ← Tool approval request card
├── lib/
│   ├── api.ts                ← All backend API calls (threads, tasks, chat, HITL)
│   └── logger.ts             ← Structured frontend logger (batches to /api/logs)
├── types/
│   └── index.ts              ← Shared TypeScript types (Thread, Message, Task, etc.)
└── contexts/
    └── ThemeContext.tsx       ← Dark/light theme context
```

---

## Development
```bash
# Start dev server (port 3000)
pnpm dev

# Production build (validates TypeScript)
pnpm build

# Add a dependency
pnpm add package-name

# Add a dev dependency
pnpm add -D package-name

# After adding Prisma models
npx prisma generate
```

---

## Tailwind CSS v4 — CRITICAL RULES
Tailwind v4 changed CSS variable syntax. **Always use the new format:**

| ❌ Old (v3) | ✅ New (v4) |
|---|---|
| `bg-[var(--background)]` | `bg-background` |
| `text-[var(--foreground)]` | `text-foreground` |
| `border-[var(--border)]` | `border-(--border)` |
| `bg-[var(--card)]` | `bg-(--card)` |
| `hover:bg-[var(--card-hover)]` | `hover:bg-(--card-hover)` |
| `bg-[var(--input-bg)]` | `bg-(--input-bg)` |
| `bg-gradient-to-br` | `bg-linear-to-br` |
| `flex-shrink-0` | `shrink-0` |
| `flex-grow` | `grow` |
| `w-[480px]` | `w-120` |
| `max-w-[120px]` | `max-w-30` |

**Rule:** For any CSS custom property reference in a Tailwind class, use
`property-(--var-name)` not `property-[var(--var-name)]`.
For named design tokens (background, foreground), use the short form: `bg-background`.

---

## SSE Event Types (from backend)
All events arrive on the `/api/chat` SSE stream. Handle in `page.tsx`:

| `type` | Payload | Action |
|---|---|---|
| `text_delta` | `{content, partial}` | Append to assistant message |
| `reasoning_delta` | `{content, partial}` | Show reasoning bubble |
| `tool_call` | `{tool_name, input}` | Show tool call in message |
| `tool_result` | `{tool_name, result, metadata}` | Render tool result / app widget |
| `tool_approval_request` | `{requestId, tool_name, input}` | Show `<ToolApprovalCard>` |
| `human_input_request` | `{requestId, prompt, options}` | Show `<HumanInputCard>` |
| `task_list_created` | `{task_list}` | `setTaskList(...)` |
| `task_updated` | `{task_list_id, task}` | Update task in state |
| `task_added` | `{task_list_id, task}` | Append task to list |
| `task_deleted` | `{task_list_id, task_id}` | Remove task from list |
| `completion` | `{message}` | Finalize assistant turn |
| `error` | `{message}` | Show error toast |

---

## API Client (`src/lib/api.ts`)
All backend calls go through the `api` object. Never call `fetch` directly in components:
```ts
import { api } from "@/lib"

// Threads
const threads = await api.getThreads()
const thread  = await api.createThread("My Chat")
await api.deleteThread(id)

// Tasks
const board = await api.getTaskList(conversationId)
await api.updateTask(listId, taskId, { status: "done" })
await api.addTasks(listId, ["New task"])
await api.deleteTask(listId, taskId)
```

---

## Component Conventions
- Every component is a named export function (no default exports for components).
- Props use inline TypeScript interfaces defined above the component.
- Never use `any` — if the type is unknown, use `unknown` and narrow it.
- `"use client"` directive at the top for interactive components.
- CSS variables for all theme colours — never hardcode hex values in className.
- KanbanPanel: receives `taskList`, emits callbacks upward — it's a pure presentational component.

---

## Environment Variables (`.env.local`)
```
NEXT_PUBLIC_API_URL=http://localhost:8000
BACKEND_API_URL=http://localhost:8000     # server-side only (Next.js API routes)
GOOGLE_CLIENT_ID=...       # optional, for Google OAuth
GOOGLE_CLIENT_SECRET=...   # optional
SPOTIFY_CLIENT_ID=...      # optional
SPOTIFY_CLIENT_SECRET=...  # optional
DATABASE_URL=...            # Prisma (for session / OAuth)
```

### Docker / K8s Build Rules
`NEXT_PUBLIC_API_URL` is **baked at build time** into the JS bundle.
- **Local dev**: `http://localhost:8000`
- **k8s (Kind)**: `""` (empty string) — browser uses relative paths, ingress routes to gateway
- Pass via: `docker build --build-arg NEXT_PUBLIC_API_URL="" ...`
- `BACKEND_API_URL` is read at runtime (server-side only) — safe to set via k8s env vars.

---

## Frontend Logger (`src/lib/logger.ts`)
Structured logger singleton. Only `warn`/`error` are batched to `/api/logs` for Loki collection.
```ts
import { logger } from "@/lib/logger";

logger.info("Thread opened", { component: "Sidebar", action: "open_thread" });
logger.error("SSE failed", { component: "ChatStream", metadata: { status: 500 } });
```

---

## CI/CD

GitHub Actions workflow in `.github/workflows/ci.yml`:
- **Lint**: ESLint
- **Type check**: `tsc --noEmit`
- **Build**: Next.js production build (with `NEXT_PUBLIC_API_URL=""`)
- **Docker**: Image to GHCR with BuildKit cache
- **Security**: `pnpm audit`
