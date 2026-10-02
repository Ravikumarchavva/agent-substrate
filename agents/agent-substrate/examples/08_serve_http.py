"""Serve an agent over HTTP.

``add_routes`` mounts one endpoint on a FastAPI app you own: ``POST /chat`` with ``{"message", "thread_id"}`` streams the
run back as Server-Sent Events — the same wire protocol the Substrate chat UI speaks. Auth, CORS, other routes: you add
them to your app the way you would anywhere else.

    uv run python examples/08_serve_http.py            # a quick in-process demo of the stream
    uv run python examples/08_serve_http.py --serve    # a real server on http://127.0.0.1:8000
      curl -N localhost:8000/chat -H 'content-type: application/json' -d '{"message": "hi", "thread_id": "t1"}'
"""

from __future__ import annotations

import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from _model import pick_model
from fastapi import FastAPI

from substrate.agents import ReActAgent
from substrate.runtime import Runtime
from substrate.serve import add_routes


def build_app() -> FastAPI:
    runtime = Runtime.open("./.substrate")
    agent = ReActAgent(
        "assistant",
        model=pick_model("Hello from the server."),
        system_instructions="You are a concise assistant.",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        async with runtime:  # starts the worker on startup, stops it (and closes the store) on shutdown
            yield

    app = FastAPI(lifespan=lifespan)
    add_routes(app, runtime, agent, path="/chat")
    return app


def demo() -> None:
    from fastapi.testclient import TestClient

    with TestClient(build_app()) as client:
        with client.stream("POST", "/chat", json={"message": "hi", "thread_id": "demo"}) as response:
            for line in response.iter_lines():
                if line:
                    print(line)


if __name__ == "__main__":
    if "--serve" in sys.argv:
        import uvicorn

        uvicorn.run(build_app(), host="127.0.0.1", port=8000)
    else:
        demo()
