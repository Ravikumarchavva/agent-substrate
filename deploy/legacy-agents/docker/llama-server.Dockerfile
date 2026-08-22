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
    build-essential cmake git ca-certificates libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
# NOT actually pinned to a commit despite this comment's original framing —
# `git clone --depth 1` with no ref just takes whatever HEAD is at build
# time. Real, found-not-assumed consequence, hit while building
# llama-server.gpu.Dockerfile from a fresh clone: a newer llama.cpp commit
# has deprecated LLAMA_CURL entirely ("LLAMA_CURL is deprecated and will be
# ignored" at configure time), which silently falls through to a TLS-less
# HF fetcher that can't resolve any --hf-repo download at all — see the
# LLAMA_OPENSSL flag below, which is the actual fix, not just a comment
# correction. unpin only after confirming a newer commit still passes this
# project's own verification (real embed/rerank calls with sane,
# correctly-shaped output — see the plan's Verification section, not just
# "it compiles").
RUN git clone --depth 1 https://github.com/ggml-org/llama.cpp .

# CPU-native build, no CUDA/GPU backend — matches this deployment's actual
# hardware. LLAMA_OPENSSL=ON (not the deprecated LLAMA_CURL=ON): real,
# found-not-assumed — confirmed via an actual failed `docker run` against
# this exact flag combination that LLAMA_CURL is silently ignored on
# current llama.cpp, and the resulting TLS-less fallback HF fetcher can't
# resolve `--hf-repo` downloads at all ("HTTPS is not supported... rebuild
# with -DLLAMA_BUILD_BORINGSSL/-DLLAMA_OPENSSL"). LLAMA_OPENSSL needs
# libssl-dev (OpenSSL dev headers), not libcurl4-openssl-dev — this build
# no longer links against curl for the HF downloader.
RUN cmake -B build -DGGML_NATIVE=ON -DCMAKE_BUILD_TYPE=Release -DLLAMA_OPENSSL=ON \
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
