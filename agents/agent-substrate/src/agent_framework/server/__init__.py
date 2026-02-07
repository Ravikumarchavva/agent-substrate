"""Agent Framework Chat Server.

Production-grade FastAPI server with:
- Session management (threads)
- Message persistence (PostgreSQL)
- Per-session agent memory
- Lifecycle hooks (on_chat_start, on_message, on_chat_end, on_chat_resume)
- SSE streaming with typed chunks
"""
