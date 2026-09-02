# ============================================================
# Stage 1: Build PicoClaw + official WebUI launcher
# ============================================================
FROM node:22.20-bookworm AS picoclaw-builder

ARG GO_VERSION=1.25.11
ARG PNPM_VERSION=11.25.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git make gcc g++ python3 \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" -o /tmp/go.tgz \
    && rm -rf /usr/local/go \
    && tar -C /usr/local -xzf /tmp/go.tgz \
    && rm -f /tmp/go.tgz

ENV PATH="/usr/local/go/bin:/go/bin:${PATH}" \
    GOPATH="/go" \
    GOTOOLCHAIN="local"

RUN npm install -g "pnpm@${PNPM_VERSION}" \
    && pnpm --version \
    && go version

WORKDIR /src/picoclaw

# The small release marker is updated automatically by GitHub Actions when
# a newer PicoClaw release appears. This makes every build reproducible while
# still allowing unattended version bumps.
COPY .picoclaw-release /tmp/picoclaw-release
RUN PICOCLAW_VERSION="$(cat /tmp/picoclaw-release)" \
    && test -n "${PICOCLAW_VERSION}" \
    && echo "Building PicoClaw ${PICOCLAW_VERSION}" \
    && git clone --depth 1 --branch "${PICOCLAW_VERSION}" https://github.com/sipeed/picoclaw.git .

RUN cd web/frontend \
    && pnpm install --frozen-lockfile

RUN make build
RUN make build-launcher
RUN test -x build/picoclaw && test -x build/picoclaw-launcher

# ============================================================
# Stage 2: Build current llama.cpp CPU server
# ============================================================
FROM debian:bookworm-slim AS llama-builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git cmake build-essential python3 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src/llama.cpp

# Qwen3.5 support landed in llama.cpp during 2026. Build current master so
# the bundled server is never stuck on an old release that cannot parse Qwen3.5.
RUN git clone --depth 1 https://github.com/ggml-org/llama.cpp.git . \
    && cmake -S . -B build \
        -DCMAKE_BUILD_TYPE=Release \
        -DGGML_NATIVE=OFF \
        -DLLAMA_BUILD_TESTS=OFF \
        -DLLAMA_BUILD_EXAMPLES=OFF \
        -DLLAMA_BUILD_TOOLS=ON \
        -DLLAMA_BUILD_SERVER=ON \
        -DLLAMA_BUILD_APP=OFF \
        -DLLAMA_BUILD_WEBUI=OFF \
    && cmake --build build --config Release --target llama-server -j 2 \
    && test -x build/bin/llama-server \
    && build/bin/llama-server --version

# ============================================================
# Stage 3: Qwen3.5-0.8B very-small CPU quantization
# ============================================================
FROM debian:bookworm-slim AS model-downloader

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /models

# IQ2_M is intentionally selected for Render Free's 512 MB runtime. The
# larger Q4_K_M build leaves too little RAM for llama-server + context.
RUN curl -L --fail --retry 5 --retry-delay 3 --retry-all-errors \
    -o Qwen3.5-0.8B-IQ2_M.gguf \
    "https://huggingface.co/bartowski/Qwen_Qwen3.5-0.8B-GGUF/resolve/main/Qwen3.5-0.8B-IQ2_M.gguf" \
    && test -s Qwen3.5-0.8B-IQ2_M.gguf

# ============================================================
# Stage 4: Runtime - official PicoClaw WebUI + Gateway + Qwen
# ============================================================
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=picoclaw-builder /src/picoclaw/build/picoclaw /app/picoclaw
COPY --from=picoclaw-builder /src/picoclaw/build/picoclaw-launcher /app/picoclaw-launcher
COPY --from=llama-builder /src/llama.cpp/build/bin/llama-server /app/llama-server

RUN mkdir -p /app/models
COPY --from=model-downloader /models/Qwen3.5-0.8B-IQ2_M.gguf /app/models/Qwen3.5-0.8B-IQ2_M.gguf

COPY config/ /config/
COPY scripts/ /app/scripts/
COPY entrypoint.sh /entrypoint.sh

RUN chmod +x /app/picoclaw /app/picoclaw-launcher /app/llama-server \
        /app/scripts/model_supervisor.sh /entrypoint.sh \
    && ln -sf /app/picoclaw /usr/local/bin/picoclaw \
    && ln -sf /app/picoclaw-launcher /usr/local/bin/picoclaw-launcher \
    && ln -sf /app/llama-server /usr/local/bin/llama-server \
    && /app/llama-server --version

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 \
    CMD /bin/sh -c 'curl -fsS "http://127.0.0.1:${PORT:-8080}/" >/dev/null || exit 1'

ENTRYPOINT ["/bin/sh", "/entrypoint.sh"]
