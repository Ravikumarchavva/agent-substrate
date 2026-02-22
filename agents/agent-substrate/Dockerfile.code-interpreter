# Dockerfile.code-interpreter — Firecracker Code Interpreter Service
#
# Build:   docker build -f Dockerfile.code-interpreter -t code-interpreter:latest .
# Run:     docker run --privileged --device /dev/kvm -v /path/to/data:/data -p 8080:8080 code-interpreter:latest
#
# NOTE: Requires --privileged for Firecracker KVM access.

FROM python:3.13-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates iproute2 iptables \
    && rm -rf /var/lib/apt/lists/*

ARG FC_VERSION=1.14.1
RUN ARCH=$(uname -m) && \
    curl -fsSL \
      "https://github.com/firecracker-microvm/firecracker/releases/download/v${FC_VERSION}/firecracker-v${FC_VERSION}-${ARCH}.tgz" \
      | tar xz -C /tmp && \
    mv "/tmp/release-v${FC_VERSION}-${ARCH}/firecracker-v${FC_VERSION}-${ARCH}" \
       /usr/local/bin/firecracker && \
    chmod +x /usr/local/bin/firecracker && \
    rm -rf /tmp/release-*

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv pip install --system -e .

RUN mkdir -p /data /tmp/firecracker-vms

EXPOSE 8080

HEALTHCHECK --interval=15s --timeout=5s --start-period=45s --retries=3 \
    CMD curl -f http://localhost:8080/v1/health || exit 1

ENTRYPOINT ["uvicorn", "agent_framework.code_interpreter_service.app:app"]
CMD ["--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--log-level", "info"]
