# Agent Lifecycle

Raavan’s default execution model is the ReAct loop: Think → Act → Observe.

## Loop phases

### Think

The model receives the current prompt context and decides whether it can answer directly or needs tools.

### Act

If tool calls are returned, the runtime validates arguments and executes tools in order.

### Observe

Tool results are appended to memory so the next model call can incorporate what happened.

## Guardrails

The loop has three natural control points:

- input validation before the model call
- tool-call validation before execution
- output checks before the final response is returned

## Why it matters

This structure keeps the agent predictable, inspectable, and easy to stream to a UI.