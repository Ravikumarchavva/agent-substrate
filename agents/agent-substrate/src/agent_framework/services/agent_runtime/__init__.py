"""Agent Runtime Service — Wave 3.

Owns: agent instantiation, ReAct loop execution, memory management.
Does not own: thread/step persistence (delegates to Conversation),
workflow lifecycle (takes commands from Workflow Orchestrator),
tool execution (delegates to Tool Executor).
"""
