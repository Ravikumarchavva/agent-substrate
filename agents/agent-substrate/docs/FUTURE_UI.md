# Future UI: mdocUI vs MCP Apps

Researched: April 2026 · Source: https://github.com/mdocui/mdocui

---

## What Is mdocUI?

mdocUI is a **generative UI library for LLMs** (alpha, v0.6.x).
The LLM writes normal Markdown and drops `{% %}` Markdoc-style component tags inline.
A streaming parser separates the text into prose nodes and component nodes as tokens
arrive, and a React renderer maps them to live components.

```
The Q4 results show strong growth.

{% chart type="bar" labels=["Jan","Feb","Mar"] values=[120,150,180] /%}

Revenue grew **12%** quarter-over-quarter.

{% button action="continue" label="Show by region" /%}
```

---

## mdocUI Architecture

| Layer | Role |
|---|---|
| `@mdocui/core` | Streaming tokenizer + parser, component registry (Zod-validated), prompt generator |
| `@mdocui/react` | React renderer, 24 built-in components, `useRenderer` hook |
| `@mdocui/cli` | Scaffold new components, preview, CI |

### Component catalogue (built-in)

**Layout**: `stack`, `grid`, `card`, `divider`, `accordion`, `tabs`  
**Interactive**: `button`, `button-group`, `input`, `textarea`, `select`, `checkbox`, `toggle`, `form`  
**Data**: `chart`, `table`, `stat`, `progress`  
**Content**: `callout`, `badge`, `image`, `code-block`, `link`

### Key design decisions

- `{% %}` delimiters never appear in normal prose or fenced code — the streaming parser
  can detect them without lookahead or backtracking.
- Components render theme-neutral semantic HTML with `data-mdocui-*` attributes and
  use `currentColor` — they adapt to any host theme automatically.
- A single `onAction` callback routes all interactive events (button clicks, form
  submits, link opens).
- `generatePrompt(registry, opts)` auto-generates the LLM system prompt from the
  component registry — no manual prompt maintenance.

---

## Our Current MCP Apps System

The raavan framework has its own LLM-driven UI system built on MCP.

### How it works

1. A tool subclasses `McpAppTool` and declares `ui_resource_uri = "ui://tool_name"`.
2. On execution the tool returns a `ToolResult` with `_meta.ui.resourceUri` set.
3. The backend's SSE `tool_result` event includes `has_app: true` and `http_url`.
4. `page.tsx` calls `openInPanel()` which adds an entry to `AppPanel`.
5. `AppPanel.tsx` loads the tool's HTML into a **sandboxed `<iframe>`** and keeps
   it mounted even when the tab is backgrounded.
6. `McpAppRenderer.tsx` uses JSON-RPC `postMessage` to push updated context
   (`ui/notifications/tool-input`) into the iframe whenever tool args change.
7. The iframe can call `submitResult` to send data back to the LLM.

### Current built-in app tools

| Tool | What it renders |
|---|---|
| `data_visualizer` | Bar / line / pie charts from `{label, value}` arrays |
| `spotify_player` | OAuth-authenticated Spotify player widget |
| `kanban_board` | Task management Kanban driven by `task_updated` SSE events |
| `json_explorer` | Tree-view navigator for nested JSON objects |
| `markdown_previewer` | Real-time Markdown rendering |
| `color_palette` | Visual color picker / design tool |

---

## Side-by-Side Comparison

| Dimension | mdocUI | Our MCP Apps |
|---|---|---|
| **UI authoring** | LLM writes `{% component /%}` tags inline | Tool author writes HTML/JS bundle pre-shipped |
| **Layout generation** | Dynamic — LLM decides layout per response | Static — layout is hardcoded per tool |
| **Rendering** | Components in host React DOM, no iframe | Sandboxed `<iframe>`, separate HTML context |
| **Theming** | `currentColor` + `classNames` prop, zero hardcoded colours | iframe CSS isolated from host |
| **Streaming** | Character-by-character parser, shimmer placeholders, animations | Full HTML loaded after tool call completes |
| **Bidirectionality** | `onAction` → new user message or form submit | `submitResult` postMessage → back to LLM |
| **Inline vs panel** | Inline in the chat message itself | Sliding side panel, separate from chat |
| **Host app coupling** | Direct React component swap (`components` prop) | Full JS bundle per tool, deployed separately |
| **System prompt** | Auto-generated from registry via `generatePrompt()` | Not required — tool description covers it |
| **Customisation** | Swap any component: `components={{ button: MyButton }}` | Rewrite the entire HTML bundle |
| **Security boundary** | Same DOM origin, no sandbox | Sandboxed iframe — good for untrusted widgets |
| **Maturity** | Alpha (v0.6.x, ~11 Github stars, 1 contributor) | Shipped in this repo |

---

## When Each Approach Wins

### Use MCP Apps when:
- The tool needs a **rich, pre-built application** (Spotify player, code editor,
  canvas-based drawing, video).
- You need **iframe isolation** — the tool code must run in its own origin / sandbox.
- The UI is **stateful and persistent** across multiple agent turns (e.g. Kanban board).
- The tool is authored by a third party (MCP server) — you don't control the host app.
- The widget has **heavy JS dependencies** that shouldn't pollute the host bundle.

### Use mdocUI when:
- You want the **LLM to dynamically compose the layout** (charts, stats, tables, buttons)
  without pre-building a bundle per tool.
- The UI belongs **inside the chat message** itself, not a side panel.
- You want the LLM to **mix prose and components** in the same response seamlessly.
- You want a **low-friction component swap** using your existing design system
  (Shadcn, Radix, etc.) without writing full HTML bundles.
- You need **streaming shimmer / loading states** while the response arrives.

---

## What We'd Need to Add mdocUI to raavan-ui

The two systems are complementary, not mutually exclusive. mdocUI would live in the
chat message stream; MCP Apps stay in the side panel.

### Backend changes

1. **System prompt injection** — `ReActAgent` or `AgentService` reads a
   `MDOCUI_SYSTEM_PROMPT` env var (or calls `generatePrompt()` from a Node side-car)
   and prepends it to the system message.

   Short-term workaround: store the prompt as a skill in
   `catalog/skills/mdocui/SKILL.md` and inject via `SkillManager`.

2. **No structural changes** to SSE — the LLM's `text_delta` events already carry
   the raw markdown+tags string. The parser runs entirely on the frontend.

### Frontend changes (`raavan-ui`)

1. **Add packages**
   ```
   pnpm add @mdocui/core @mdocui/react
   ```

2. **Add `useRenderer` to the SSE hook in `page.tsx`**
   ```ts
   const { nodes, isStreaming, push, done } = useRenderer({ registry })
   // inside text_delta handler:
   push(event.content)
   // inside completion handler:
   done()
   ```

3. **Update `MessageBubble.tsx`**
   - When `message.role === "assistant"` and the message has renderer nodes →
     render `<Renderer nodes={nodes} components={defaultComponents} onAction={...} />`
     instead of the current markdown string.
   - For already-complete messages (history), fall back to the existing markdown
     renderer.

4. **Wire `onAction`**
   ```ts
   onAction={(event) => {
     if (event.action === "continue") sendMessage(event.label)
     if (event.action.startsWith("submit:")) submitForm(event.formState)
   }}
   ```

5. **Swap to your design system** (optional)
   ```ts
   const components = { ...defaultComponents, button: YourButton, card: YourCard }
   ```

### Effort estimate

| Task | Size |
|---|---|
| Install + basic renderer in `MessageBubble` | Small (1–2 days) |
| `onAction` wiring | Small (half day) |
| Backend system-prompt injection via skill | Small (half day) |
| Design-system component swap | Medium (depends on depth) |
| Streaming shimmer / animation integration | Small (built-in) |
| Per-component `classNames` for Tailwind | Small |

---

## Open Questions

- Should mdocUI components inherit the host Tailwind dark/light theme via CSS variables,
  or should we configure them with explicit `classNames`?
  → Prefer CSS variable inheritance (`currentColor`) + override only what deviates.

- Should `onAction: "continue"` send the label as a *user* message (visible in chat) or
  as a *hidden* tool call?
  → Consistent with HITL UX: send as a visible user message so the history is auditable.

- How do we sync mdocUI component state with the existing `thread` / `message` model in
  the DB? Components are ephemeral (not persisted).
  → Persist only final text output in `Message.content`; component state is local to the
  session. On thread restore, fall back to markdown rendering.
