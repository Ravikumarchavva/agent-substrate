# docker/llama-server.gpu.Dockerfile — CUDA llama.cpp server for the
# Qwen3-VL-Embedding-2B / Qwen3-VL-Reranker-2B sidecars, GPU variant.
#
# Same service as llama-server.Dockerfile, just built with -DGGML_CUDA=ON
# instead of CPU-native — for local dev on an NVIDIA GPU, not for cheap
# hosting (that's what llama-server.Dockerfile/the default profiles are
# for). Requires the host's Docker to have the NVIDIA Container Toolkit
# configured (`docker info` lists an `nvidia` runtime) and a driver new
# enough for CUDA 13.0 — verify with `nvidia-smi` on the host before
# building.
#
# Real, unverified risk, named plainly rather than assumed away: this repo
# has never actually done a from-source CUDA llama.cpp compile before this
# file. -DGGML_CUDA=ON is a mature, standard llama.cpp flag (confirmed via
# research, not assumed) with no known CUDA-specific bugs in --embedding/
# --reranking serving modes, but that's not the same as this exact build
# having been run and its output verified. Verify with a real `docker
# build` + real embed/rerank calls against actual output shape (same
# standard llama-server.Dockerfile's own CPU build already holds itself
# to) before trusting this in anything that matters. Separately: real
# multimodal (image) embedding support for Qwen3-VL-Embedding in llama.cpp
# is still community/bleeding-edge upstream (an unmerged draft PR as of
# this file's research) — re-verify real image embeddings specifically,
# not just text, after a build.
#
# Build:   docker build -f docker/llama-server.gpu.Dockerfile -t llama-server-gpu:latest .
# Run:     docker run --gpus all -e LLAMA_CACHE=/cache -v llama-cache:/cache llama-server-gpu:latest \
#            -hf Rizwan313/Qwen3-VL-Embedding-2B-GGUF:Q4_K_M --embedding --pooling last -ngl 99

# devel (not cudnn-runtime, which document-intelligence.gpu.Dockerfile
# uses) — compiling llama.cpp's CUDA kernels needs nvcc, which only the
# devel image ships. Same CUDA version (13.0.3) as
# document-intelligence.gpu.Dockerfile for consistency — this repo has
# already vetted CUDA 13.0 against the dev GPU's driver (CUDA 13.1), no
# reason to introduce a second CUDA version to track.
FROM nvidia/cuda:13.0.3-cudnn-devel-ubuntu24.04 AS build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential cmake git ca-certificates libssl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
# NOT actually pinned to a commit today, despite the aspirational comment
# below — `git clone --depth 1` with no ref just takes whatever HEAD is at
# build time. Real, found-not-assumed consequence: this build hit a live
# upstream regression from that (see the LLAMA_OPENSSL note right below),
# unrelated to CUDA at all. Flagged in llama-server.Dockerfile too, since
# it has the exact same non-enforced "pin" and will break identically on
# its next fresh build.
RUN git clone --depth 1 https://github.com/ggml-org/llama.cpp .

# GGML_CUDA=ON is the only new flag versus llama-server.Dockerfile's cmake
# invocation — GGML_NATIVE stays on for CPU-side ops in mixed CPU/GPU
# offload. LLAMA_OPENSSL=ON (not LLAMA_CURL=ON, which llama-server.Dockerfile
# uses) for the same --hf-repo/--hf-file runtime GGUF download support:
# real, found-not-assumed — this build's cloned commit has deprecated
# LLAMA_CURL entirely ("LLAMA_CURL is deprecated and will be ignored" at
# configure time), which silently fell through to a TLS-less HF fetcher
# that can't resolve any --hf-repo download at all ("HTTPS is not
# supported... rebuild with -DLLAMA_BUILD_BORINGSSL/-DLLAMA_OPENSSL") —
# confirmed via real docker run, not caught by the build succeeding.
# LLAMA_OPENSSL needs libssl-dev (OpenSSL dev headers), not
# libcurl4-openssl-dev — this build no longer links against curl.
#
# Symlink the CUDA driver stub into the default system linker search path:
# real, found-not-assumed build failure — libggml-cuda.so needs Driver API
# symbols (cuMemCreate, cuDeviceGet, ...) at link time, but this build
# container has no real GPU driver (that only gets mounted at container
# *run* time via the NVIDIA Container Toolkit). The devel image ships a
# stub libcuda.so exactly for this (verified present at
# /usr/local/cuda/lib64/stubs/libcuda.so). Two other approaches were tried
# and did NOT work: `ENV LIBRARY_PATH=...` (that gcc-frontend env var isn't
# read by this particular link step) and passing `-L.../stubs` via
# `-DCMAKE_*_LINKER_FLAGS` (still failed — flag-ordering relative to where
# `-lcuda` gets referenced in CMake's generated link command isn't
# guaranteed). Symlinking into the default system library path + ldconfig
# sidesteps both problems entirely — `ld` finds it via the normal system
# search path, no per-target flag ordering to get right.
RUN ln -s /usr/local/cuda/lib64/stubs/libcuda.so /usr/lib/x86_64-linux-gnu/libcuda.so.1 \
    && ldconfig
# GGML_NATIVE=ON only auto-detects CPU features here, not the CUDA
# architecture — confirmed real: without a GPU visible at build time (no
# --gpus flag on a plain `docker build`), nvcc's own '-arch=native' can't
# detect anything and silently falls back to some generic default,
# risking a binary that isn't actually optimized for (or worse, isn't
# compatible with) the real deploy target. Explicit
# CMAKE_CUDA_ARCHITECTURES=86 targets Ampere/SM 8.6 — this project's own
# dev GPU (RTX 3050 Laptop) — rather than trusting build-time auto-detection
# that has nothing to detect.
# BUILD_JOBS defaults to nproc (full parallelism — right for CI, which
# isn't thermally constrained) but is overridable via
# `--build-arg BUILD_JOBS=N` for local builds on hot/thermally-limited
# hardware.
ARG BUILD_JOBS=""
RUN cmake -B build -DGGML_NATIVE=ON -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release -DLLAMA_OPENSSL=ON \
    -DCMAKE_CUDA_ARCHITECTURES=86 \
    && cmake --build build -j"${BUILD_JOBS:-$(nproc)}" --target llama-server

# cudnn-runtime (not devel) — smaller, has the CUDA/cuDNN shared libs the
# compiled binary needs at runtime, no compiler toolchain. Matches
# document-intelligence.gpu.Dockerfile's own runtime base exactly.
FROM nvidia/cuda:13.0.3-cudnn-runtime-ubuntu24.04 AS runtime

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
# llama-server.Dockerfile's own healthcheck.
HEALTHCHECK --interval=15s --timeout=5s --start-period=180s --retries=3 \
    CMD curl -f http://localhost:${PORT:-8080}/health || exit 1

ENTRYPOINT ["llama-server"]
