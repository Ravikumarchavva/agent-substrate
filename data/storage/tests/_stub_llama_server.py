#!/usr/bin/env python3
"""Stub standing in for the real ``llama-server`` binary in
``test_llama_pool.py``. Ignores every arg ``LocalLlamaServerPool`` passes
(``-m``, ``--mmproj``, ``--host``, ``-ngl``, ...) except ``--port``, and
serves a hardcoded HTTP 200 to any request (the pool only ever hits
``GET /health``). No real HTTP parsing needed — enough of the wire format to
satisfy httpx as a client.
"""

import asyncio
import sys


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        await reader.readline()  # request line
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b""):
                break
        body = b"ok"
        response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"Connection: close\r\n\r\n" + body
        )
        writer.write(response)
        await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


async def _main() -> None:
    args = sys.argv[1:]
    port = 8090
    if "--port" in args:
        port = int(args[args.index("--port") + 1])
    server = await asyncio.start_server(_handle, "127.0.0.1", port)
    print(f"stub-llama-server listening on {port}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(_main())
