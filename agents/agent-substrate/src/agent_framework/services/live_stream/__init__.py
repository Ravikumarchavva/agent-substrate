"""Live Stream Service — Wave 4.

Owns: SSE stream composition, event fan-out to clients, per-thread stream state.
Does not own: agent execution, HITL approval, conversation persistence.
"""
