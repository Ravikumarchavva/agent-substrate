# nsjail + the libraries it needs, on a slim Python base. Built FROM agent-substrate's own nsjail stage
# (deployment/docker/backend.Dockerfile --target nsjail-builder), so the binary is the one production uses.
ARG NSJAIL_BUILDER=substrate-nsjail-builder
FROM ${NSJAIL_BUILDER} AS nsjail
FROM python:3.14-slim
RUN apt-get update && apt-get install -y --no-install-recommends libprotobuf32 libnl-route-3-200 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=nsjail /nsjail/nsjail /usr/local/bin/nsjail
