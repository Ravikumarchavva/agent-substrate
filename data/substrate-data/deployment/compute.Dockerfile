# Attached-compute worker image. Operators run it wherever they host compute and attach it to the
# controller; the application never launches it. Configure entirely through SUBSTRATE_DATA_* env:
# worker S3 identity, WORKER_TOKEN, TICKET_PUBLIC_KEY_FILE (mounted), endpoints, memory limit.
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
WORKDIR /opt/substrate
COPY deployment/compute.requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r compute.requirements.txt && pip install --no-cache-dir uv_build==0.10.11
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps --no-build-isolation .
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/opt/substrate/home \
    SUBSTRATE_DATA_WORKER_BIND_HOST=0.0.0.0
# DuckDB extensions are installed at build time: workers never download code at runtime.
RUN mkdir -p /opt/substrate/home && python -c "import duckdb; duckdb.connect().execute('INSTALL httpfs')" \
    && chown -R 65532:65532 /opt/substrate/home
USER 65532:65532
EXPOSE 8011
ENTRYPOINT ["python", "-m", "substrate_data.compute.worker"]
CMD ["sql", "8011"]
