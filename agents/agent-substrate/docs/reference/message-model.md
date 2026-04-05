# Message Model

Raavan treats messages as typed execution artifacts, not just free-form strings.

## Important message types

- `SystemMessage`
- `UserMessage`
- `AssistantMessage`
- `ToolCallMessage`
- `ToolExecutionResultMessage`

## Important details

- `UserMessage` can contain text and media content.
- `AssistantMessage` may contain text, tool calls, or both.
- `ToolCallMessage` uses `name` and `arguments` fields.
- tool execution results are appended back into the conversation for the next model step.

## Why this matters

These message shapes are the contract between the agent loop, memory system, provider adapters, and the UI streaming layer.