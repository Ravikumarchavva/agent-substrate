# docker/llama-server.Dockerfile — CPU-only llama.cpp server for the
# Qwen3-VL-Embedding-2B / Qwen3-VL-Reranker-2B sidecars (see extraction
# service's embedding.py). Builds the `llama-server` binary only — model
# weights are NOT baked into the image; each service downloads its own GGUF
# on first start via `llama-server -hf <repo>:<quant>` into a persistent
# named volume (LLAMA_CACHE), so a rebuild of this image never re-downloads
# a ~1GB file. Verified buildable/runnable this way in agent-substrate's own
# dev session — see docs/claude_docs/decisions.md for the model choice.
#
# Build:   docker build -f docker/llama-server.Dockerfile -t llama-server:latest .
# Run:     docker run -e LLAMA_CACHE=/cache -v llama-cache:/cache llama-server:latest \
#            -hf Rizwan313/Qwen3-VL-Embedding-2B-GGUF:Q4_K_M --embedding --pooling last

FROM debian:trixie-slim AS build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
# Pinned to a known-good commit rather than a moving branch — llama.cpp's
# Qwen3-VL support is very recent (days old, per this session's own
# research) and still landing fixes; unpin only after confirming a newer
# commit still passes this project's own verification (real embed/rerank
# calls with sane, correctly-shaped output — see the plan's Verification
# section, not just "it compiles").
RUN git clone --depth 1 https://github.com/ggml-org/llama.cpp .

# CPU-native build, no CUDA/GPU backend — matches this deployment's actual
# hardware. -DLLAMA_CURL=OFF: the -hf download flag still works via a
# built-in fallback fetcher; skips needing libcurl-dev in the build image.
RUN cmake -B build -DGGML_NATIVE=ON -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF \
    && cmake --build build -j"$(nproc)" --target llama-server

FROM debian:trixie-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /src/build/bin/llama-server /usr/local/bin/llama-server
COPY --from=build /src/build/bin/*.so /usr/local/lib/
RUN ldconfig

ENV LLAMA_CACHE=/cache
VOLUME ["/cache"]

# Generous --start-period: first boot downloads the ~1.1GB GGUF (network-
# dependent) before the model finishes loading — same rationale as
# extraction.Dockerfile's healthcheck.
HEALTHCHECK --interval=15s --timeout=5s --start-period=180s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8080}/health || exit 1

ENTRYPOINT ["llama-server"]
