"""Serve an agent over HTTP: ``create_app(agent)``.

One call gives you a FastAPI app with:

* ``POST /chat``                          — the run as Server-Sent Events, in the engine's own wire protocol
* ``POST /agui``                          — the same run as AG-UI events, for front ends that speak it (CopilotKit…)
* ``GET  /runs/{id}/events``              — follow a run again, e.g. after a dropped connection
* ``POST /runs/{id}/cancel``, ``GET|POST /runs/{id}/approvals…`` — control and human approval

Closing a connection detaches from the run; it does not cancel it. Auth is FastAPI's own (``dependencies=[…]``).

    uv run python examples/08_serve_http.py            # a quick in-process demo of both streams
    uv run python examples/08_serve_http.py --serve    # a real server on http://127.0.0.1:8000
    substrate serve examples.08_serve_http:agent       # the same from the command line, given a module:attribute
      curl -N localhost:8000/chat -H 'content-type: application/json' -d '{"message": "hi", "thread_id": "t1"}'
"""

from __future__ import annotations

import sys

from _model import pick_model

from substrate.agents import ReActAgent
from substrate.server import create_app

agent = ReActAgent(
    "assistant",
    model=pick_model("Hello from the server."),
    system_instructions="You are a concise assistant.",
)

app = create_app(agent, store="./.substrate")


def demo() -> None:
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        print("-- /chat")
        with client.stream("POST", "/chat", json={"message": "hi", "thread_id": "demo"}) as response:
            for line in response.iter_lines():
                if line:
                    print(line)
        print("-- /agui")
        body = {"threadId": "demo-agui", "runId": "r1", "messages": [{"id": "m1", "role": "user", "content": "hi"}]}
        with client.stream("POST", "/agui", json=body) as response:
            for line in response.iter_lines():
                if line:
                    print(line)


if __name__ == "__main__":
    if "--serve" in sys.argv:
        import uvicorn

        uvicorn.run(app, host="127.0.0.1", port=8000)
    else:
        demo()
