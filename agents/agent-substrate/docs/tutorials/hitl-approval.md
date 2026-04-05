# Add HITL Approval

Human-in-the-loop is how Raavan pauses or gates risky actions.

## When to use it

- irreversible actions
- side effects in external systems
- approvals before sending emails, deleting data, or calling critical tools

## Runtime behavior

1. the agent proposes a tool call
2. the runtime inspects tool risk and HITL mode
3. an approval request event is streamed to the frontend
4. the workflow waits, continues, or auto-approves depending on mode

## Frontend path

The UI renders `ToolApprovalCard` or `HumanInputCard` from SSE events and posts the result back through the response route.

## Backend path

The durable runtime can suspend on a promise and resume after the user responds.

Read [Tools And HITL](../concepts/tools-and-hitl.md) and [Events](../reference/events.md) for the exact flow.