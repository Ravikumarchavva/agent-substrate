# docker/llama-server.Dockerfile — CPU-only llama.cpp server for the
# Qwen3-VL-Embedding-2B / Qwen3-VL-Reranker-2B sidecars (see extraction
# service's embedding.py). Builds the `llama-server` binary only — model
# weights are NOT baked into the image; each service downloads its own GGUF
# on first start via `llama-server --hf-repo/--hf-file` into a persistent
# named volume (LLAMA_CACHE), so a rebuild of this image never re-downloads
# a ~1GB file. See docs/claude_docs/decisions.md for the model choice — the
# model behavior itself was verified via a bare (non-Docker) llama-server
# build earlier in that session; this containerized build/download path was
# verified separately, after catching a real LLAMA_CURL=OFF/libgomp bug.
#
# Build:   docker build -f docker/llama-server.Dockerfile -t llama-server:latest .
# Run:     docker run -e LLAMA_CACHE=/cache -v llama-cache:/cache llama-server:latest \
#            -hf Rizwan313/Qwen3-VL-Embedding-2B-GGUF:Q4_K_M --embedding --pooling last

FROM debian:trixie-slim AS build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git ca-certificates libcurl4-openssl-dev \
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
# hardware. LLAMA_CURL=ON (default): verified this session that
# LLAMA_CURL=OFF's built-in fallback HTTPS fetcher has no TLS backend
# compiled in at all ("HTTPS is not supported... rebuild with
# -DLLAMA_BUILD_BORINGSSL/-DLLAMA_OPENSSL") — it can't resolve `--hf-repo`
# downloads, full stop. libcurl is the well-supported path instead.
RUN cmake -B build -DGGML_NATIVE=ON -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=ON \
    && cmake --build build -j"$(nproc)" --target llama-server

FROM debian:trixie-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl libgomp1 libcurl4 \
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
