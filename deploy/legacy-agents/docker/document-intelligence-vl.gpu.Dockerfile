# docker/document-intelligence-vl.gpu.Dockerfile — Document intelligence
# service, NEW `vl_gpu` mode (PaddleOCR-VL-1.6 served over HTTP by a real
# `llama-server` process; layout detection stays classic Paddle models run
# locally — see engines/paddle_vl.py's module docstring).
#
# Distinct from document-intelligence.gpu.Dockerfile, which only supports the
# OLD `ocr_classic` mode (PPStructureV3, no llama-server, no GGUF models).
# Both images can run side-by-side (see docker-compose.yml's
# document-intelligence-gpu-* replicas vs. the new document-intelligence-vl-gpu
# service) — the six pinned ocr_classic replicas stay as the explicit
# fallback path, not something this file replaces.
#
# Build:   docker build -f deployment/docker/document-intelligence-vl.gpu.Dockerfile -t document-intelligence-vl-gpu:latest .
# Run:     docker compose --profile document-intelligence-vl-gpu up
#
# Requires the host's Docker to have the NVIDIA Container Toolkit configured
# (`docker info` lists an `nvidia` runtime) and a driver new enough for CUDA
# 13.0 — verify with `nvidia-smi` on the host before building.
#
# Three stages:
#   1. llama-build  — compiles llama.cpp's `llama-server` + `llama-quantize`
#      binaries with CUDA support (same pattern as llama-server.gpu.Dockerfile).
#   2. model-build   — uses the freshly-built `llama-quantize` to bake the
#      quantized PaddleOCR-VL-1.6 GGUF files into the image at build time
#      (models.py::ensure_models's documented default path — deterministic,
#      no first-request quantization latency).
#   3. runtime       — the actual service image: `llama-server` binary +
#      baked GGUF files + the document-intelligence-gpu Python service.

# ─────────────────────────────────────────────────────────────────────────
# Stage 1: llama-build — lifted from llama-server.gpu.Dockerfile's build
# stage essentially verbatim; see that file for the full rationale behind
# each flag/workaround below (repeated here only where this stage diverges).
# ─────────────────────────────────────────────────────────────────────────
FROM nvidia/cuda:13.0.3-cudnn-devel-ubuntu24.04 AS llama-build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git ca-certificates libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Pinned to the real llama.cpp `master` HEAD commit as of the day this file
# was written (2026-09-14), confirmed via both `git ls-remote
# https://github.com/ggml-org/llama.cpp HEAD` and the GitHub API
# (api.github.com/repos/ggml-org/llama.cpp/commits/master) returning the
# same SHA from this sandbox. Fixes the real, admitted bug in
# llama-server.gpu.Dockerfile: its `git clone --depth 1` with no ref just
# takes whatever HEAD is at build time despite an aspirational "pinned"
# comment, which already caused a real breakage (LLAMA_CURL deprecation,
# see the LLAMA_OPENSSL comment below).
# TODO: this SHA is not build-verified against the actual CUDA compile
# below (no real `docker build` of this stage completed in this sandbox --
# no multi-GPU host / full network budget available) -- re-verify it
# actually builds clean, and re-pin to a newer commit periodically, before
# trusting this in real deployment.
WORKDIR /src
RUN git clone https://github.com/ggml-org/llama.cpp . && \
    git checkout 41abbfd599fbdd3470fcae0a1fb6530ad8403cd7

# LLAMA_OPENSSL=ON (not LLAMA_CURL=ON) for --hf-repo/--hf-file runtime GGUF
# download support — see llama-server.gpu.Dockerfile's comment: this
# codebase's cloned llama.cpp commits have deprecated LLAMA_CURL entirely,
# which silently produces a TLS-less HF fetcher that can't resolve any
# --hf-repo download. Needs libssl-dev (OpenSSL dev headers), already
# installed above.
#
# Symlink the CUDA driver stub into the default system linker search path —
# same real, found-not-assumed build failure as llama-server.gpu.Dockerfile:
# libggml-cuda.so needs Driver API symbols at link time, but this build
# container has no real GPU driver (only mounted at container *run* time).
RUN ln -s /usr/local/cuda/lib64/stubs/libcuda.so /usr/lib/x86_64-linux-gnu/libcuda.so.1 \
    && ldconfig

# CUDA_ARCHS is a real range, not the single dev-GPU value (86, Ampere/RTX
# 3050 Laptop) llama-server.gpu.Dockerfile hardcodes — that file's own
# comment admits 86 only targets this project's dev machine. This image is
# meant for actual multi-generation deployment, so it targets:
#   75 = Turing (T4, RTX 20xx)
#   80 = Ampere datacenter (A100)
#   86 = Ampere consumer/laptop (RTX 30xx, this project's own dev GPU)
#   89 = Ada Lovelace (RTX 40xx, L4)
#   90 = Hopper (H100)
# BUILD_JOBS defaults to nproc, overridable via --build-arg for
# thermally-constrained local builds — same as llama-server.gpu.Dockerfile.
ARG CUDA_ARCHS="75;80;86;89;90"
ARG BUILD_JOBS=""
RUN cmake -B build -DGGML_NATIVE=ON -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release -DLLAMA_OPENSSL=ON \
    -DCMAKE_CUDA_ARCHITECTURES=${CUDA_ARCHS} \
    && cmake --build build -j"${BUILD_JOBS:-$(nproc)}" --target llama-server --target llama-quantize

# ─────────────────────────────────────────────────────────────────────────
# Stage 2: model-build — bakes the quantized PaddleOCR-VL-1.6 GGUF files
# (main LM + mmproj vision encoder) into the image, using the real
# ensure_models() from src/substrate/runtimes/document_intelligence/service/
# models.py rather than reimplementing its download/quantize/naming logic.
# Reuses llama-build's own Ubuntu base (already has the CUDA driver stub
# workaround and apt cache warmed) instead of pulling in a second distinct
# base image just for this — simpler, and nvcc/cmake aren't needed here.
# ─────────────────────────────────────────────────────────────────────────
FROM llama-build AS model-build

# llama-build's own apt-get (above) installed ca-certificates but not
# curl -- needed here to fetch the uv installer, same as
# document-intelligence.gpu.Dockerfile's pattern.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"
RUN uv python install 3.13

# huggingface-hub for ensure_models() itself, plus python-json-logger/msgspec
# because models.py imports substrate.logger.setup_logging(), which needs
# both (see pyproject.toml's core deps) -- not the full document-intelligence-gpu
# extra (no paddle/torch here, this stage never runs any actual OCR/layout
# code, just the model-provisioning function).
RUN uv venv --python 3.13 /opt/venv && \
    uv pip install --python /opt/venv/bin/python \
        "huggingface-hub>=0.24.0" "python-json-logger>=2.0.7" "msgspec>=0.21.1"
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /app
# Only the one module ensure_models() actually needs (plus substrate.logger,
# which it imports) — copying the whole src tree here would drag in paddle/
# torch-importing modules this stage has no interpreter support for anyway.
COPY src/substrate/runtimes/document_intelligence/service/models.py \
    ./substrate/runtimes/document_intelligence/service/models.py
COPY src/substrate/logger.py ./substrate/logger.py
RUN touch ./substrate/__init__.py \
    ./substrate/runtimes/__init__.py \
    ./substrate/runtimes/document_intelligence/__init__.py \
    ./substrate/runtimes/document_intelligence/service/__init__.py

ARG HF_TOKEN=""
ENV HF_TOKEN=${HF_TOKEN}
ARG VL_QUANT="q8_0"
ARG VL_MODEL_DIR="/models/paddleocr-vl"
# quant="q8_0" matches ensure_models()'s own default and models.py's own
# documented finding: Q4_K_M was directly proven (real A/B test) to corrupt
# PaddleOCR-VL's structured output, so this is the only safe value to bake.
RUN PYTHONPATH=/app python -c "\
import asyncio; \
from substrate.runtimes.document_intelligence.service.models import ensure_models; \
asyncio.run(ensure_models( \
    model_dir='${VL_MODEL_DIR}', \
    quant='${VL_QUANT}', \
    llama_quantize_bin='/src/build/bin/llama-quantize', \
))"

# ─────────────────────────────────────────────────────────────────────────
# Stage 3: runtime — same pattern as document-intelligence.gpu.Dockerfile's
# single stage, plus the llama-server binary + baked GGUF models from the
# two stages above.
# ─────────────────────────────────────────────────────────────────────────
FROM nvidia/cuda:13.0.3-cudnn-runtime-ubuntu24.04 AS runtime

# Same native deps as document-intelligence.gpu.Dockerfile, plus libcurl4
# (llama-server's own runtime dep, matching llama-server.gpu.Dockerfile's
# runtime stage — even with LLAMA_OPENSSL=ON, the compiled binary still
# links libcurl for non-HF-download HTTP paths).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    libgl1 libglib2.0-0 libxcb1 libxext6 libsm6 libgomp1 libcurl4 \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"
RUN uv python install 3.13

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv venv --python 3.13 /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
RUN uv pip install --python /opt/venv/bin/python -e ".[document-intelligence-gpu]"

COPY --from=llama-build /src/build/bin/llama-server /usr/local/bin/llama-server
COPY --from=llama-build /src/build/bin/*.so /usr/local/lib/
RUN ldconfig

# Baked GGUF files -- matches ServiceConfig.vl_model_dir's default
# ("/models/paddleocr-vl", config.py) so no env var override is needed for
# the common case; LocalLlamaServerPool (llama_pool.py) reads main/mmproj
# paths resolved by ensure_models() at app startup, which will hit this
# stage's Tier-1 "already present" fast path since these files exist here.
COPY --from=model-build /models/paddleocr-vl /models/paddleocr-vl

ENV DOCUMENT_INTELLIGENCE_MODE=vl_gpu
ENV DOCUMENT_INTELLIGENCE_LLAMA_SERVER_BIN=/usr/local/bin/llama-server
ENV DOCUMENT_INTELLIGENCE_VL_MODEL_DIR=/models/paddleocr-vl

EXPOSE 8080

# --start-period=300s (not document-intelligence.gpu.Dockerfile's 90s) --
# matches llama_pool.py's own LocalLlamaServerPool.startup_timeout_s default
# (300.0). This image's startup now waits on N llama-server child processes
# each becoming healthy (one per visible GPU, via LocalLlamaServerPool),
# not just one paddle pipeline loading its weights.
HEALTHCHECK --interval=15s --timeout=5s --start-period=300s --retries=3 \
    CMD curl -f http://localhost:8080/v1/health || exit 1

ENTRYPOINT ["uvicorn", "substrate.runtimes.document_intelligence.service.app:app"]
CMD ["--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]
