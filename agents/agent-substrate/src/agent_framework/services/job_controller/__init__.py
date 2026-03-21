"""Job Controller Service — Wave 2.

Owns: durable run state, agent invocation lifecycle, cancellation, retry.
Does not own: threads, steps (delegates to Conversation Service), tool execution (delegates to Agent Runtime).
"""
