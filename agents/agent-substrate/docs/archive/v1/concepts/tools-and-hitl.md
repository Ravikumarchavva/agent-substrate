# Tools And HITL

Tools are the agent’s execution surface. HITL is the control layer around that execution.

## Tool model

- tools declare a JSON schema
- the model calls a named tool with arguments
- the framework validates and runs it
- the tool returns structured output back to the loop

## Risk and approval

Tools can be safe, sensitive, or critical. High-risk tools can require human approval before execution.

## HITL modes

- blocking wait for approval
- continue on timeout
- fire and continue

## UI integration

Approval and input requests surface as streaming events so the frontend can render an approval card or input prompt immediately.