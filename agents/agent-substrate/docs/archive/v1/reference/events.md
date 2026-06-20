# Events

The frontend UI and streaming routes rely on a stable event vocabulary.

## Common SSE events

| Event | Meaning |
|---|---|
| `text_delta` | partial assistant text |
| `reasoning_delta` | streamed reasoning summary |
| `tool_call` | the agent decided to invoke a tool |
| `tool_result` | the tool finished and returned a result |
| `tool_approval_request` | frontend must request approval |
| `human_input_request` | frontend must ask the user for structured input |
| `task_list_created` | a Kanban or task list was created |
| `task_updated` | an existing task changed |
| `task_added` | a task was added |
| `task_deleted` | a task was removed |
| `completion` | the assistant turn finished |
| `error` | the run failed or produced a terminal error |

## Design rule

Prefer factory functions and typed event models over hand-built dictionaries in runtime code.