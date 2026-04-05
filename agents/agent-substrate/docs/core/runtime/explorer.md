# Interactive Runtime Explorer

This explorer focuses on runtime internals only.

It does not include frontend, API routes, general architecture, or unrelated files. Click any actor in the embedded explorer to inspect its inputs, outputs, key methods, and its role in the runtime lifecycle.

<div class="runtime-note" markdown>

Use this when you want pinpoint understanding of how the Restate runtime is assembled: client dispatch, workflow entry, journaled activities, HITL promise suspension, worker bootstrapping, and tool execution policy.

</div>

<div class="runtime-frame">
  <iframe src="../runtime-core-explorer.html" title="Raavan Runtime Explorer"></iframe>
</div>

## Best reading order

1. Click `RestateWorkflowClient` for entrypoint dispatch.
2. Click `AgentWorkflow` to see the durable ReAct loop.
3. Click `Activities` to understand the side-effect boundary.
4. Click `Promise Gate` for HITL suspension and resume.
5. Click `Worker` to understand how everything is wired together.