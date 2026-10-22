---
applyTo: "src/app/**,src/lib/**"
description: SSE event handling, API client patterns, and chat routing
---

# App Routes, SSE & API Patterns

## Chat SSE flow
```
user sends message
  → page.tsx calls fetch POST /api/chat
    → Next.js /api/chat/route.ts proxies to backend:8001/chat
      → Backend returns EventSource stream
        → page.tsx EventSource loop dispatches events to state
```

## Handling a new SSE event type (in page.tsx)
Add a case inside the SSE event handler switch:
```ts
case "my_new_event": {
  const { field1, field2 } = data
  // update state here
  break
}
```

## API client rules (`src/lib/api.ts`)
- All `fetch` calls live in `api.ts`. Never call `fetch` directly from components.
- Always throw on non-2xx via `if (!res.ok) throw new Error(...)`.
- Always type the return value.
- Base URL is `process.env.NEXT_PUBLIC_API_URL || "http://localhost:8001"`.

## Adding a new API method
```ts
// In api.ts:
async myNewCall(param: string): Promise<MyReturnType> {
  const res = await fetch(`${API_BASE}/my-endpoint/${param}`)
  if (!res.ok) throw new Error("descriptive error")
  return res.json()
},
```

## Next.js API proxy routes (`src/app/api/`)
- `chat/route.ts` — proxies POST /api/chat → backend SSE, preserves event-stream Content-Type.
- `chat/respond/[requestId]/route.ts` — bridges HITL POST responses back to the backend.
- New proxy routes should pass through request headers and body without transformation.

## Error handling
- Catch fetch errors and display them to the user via a toast or inline error state.
- Never swallow errors silently with empty `catch {}`.

## Prisma & sessions
- Prisma client lives in `src/lib/prisma.ts`. Import it via `import prisma from "@/lib/prisma"`.
- Always run `npx prisma generate` after modifying `prisma/schema.prisma`.
- Sessions (Google OAuth) are handled with cookies via `src/lib/session.ts`.
