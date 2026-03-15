FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir "fastmcp==2.14.5" uvicorn

COPY server.py .

ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=9000

EXPOSE 9000

CMD ["python", "server.py"]
