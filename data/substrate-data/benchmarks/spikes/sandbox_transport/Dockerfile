# Built FROM the nsjail stage of agent-substrate/deployment/docker/backend.Dockerfile:
#   docker build --target nsjail-builder -t spike-nsjail-builder -f deployment/docker/backend.Dockerfile .
# Run: docker run --rm --cgroupns=host --cap-add SYS_ADMIN --security-opt seccomp=unconfined \
#   --security-opt apparmor=unconfined -v /sys/fs/cgroup:/sys/fs/cgroup:rw <image>
FROM spike-nsjail-builder AS nsjail
FROM python:3.14-slim
RUN apt-get update && apt-get install -y --no-install-recommends libprotobuf32 libnl-route-3-200 && rm -rf /var/lib/apt/lists/*
COPY --from=nsjail /nsjail/nsjail /usr/local/bin/nsjail
RUN pip install --no-cache-dir pyarrow==26.0.0 polars==1.44.2 requests
WORKDIR /spike
COPY server.py client.py run.sh ./
CMD ["bash", "run.sh"]
