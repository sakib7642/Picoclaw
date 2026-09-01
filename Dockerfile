# ============================================================
# Stage 1: Build the official PicoClaw binary + WebUI launcher
# ============================================================
FROM golang:1.25-bookworm AS picoclaw-builder

ARG PICOCLAW_VERSION=v0.3.1
ARG NODE_VERSION=22.20.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git make gcc g++ python3 \
    && rm -rf /var/lib/apt/lists/*

# PicoClaw v0.3.1 requires Go 1.25.11+ and its WebUI build requires Node 22+.
RUN curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" -o /tmp/node.tar.xz \
    && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 \
    && rm -f /tmp/node.tar.xz \
    && node --version \
    && npm --version \
    && npm install -g pnpm@10.33.0 \
    && pnpm --version

WORKDIR /src/picoclaw
RUN git clone --depth 1 --branch "${PICOCLAW_VERSION}" https://github.com/sipeed/picoclaw.git .

# Install the exact locked frontend dependencies, then build the core and launcher.
RUN cd web/frontend \
    && pnpm install --frozen-lockfile

RUN make build
RUN make build-launcher

RUN test -x build/picoclaw \
    && test -x build/picoclaw-launcher

# ============================================================
# Stage 2: Build PicoLM C inference engine
# ============================================================
FROM debian:bookworm-slim AS picolm-builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git make gcc g++ libc6-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --depth 1 https://github.com/RightNow-AI/picolm.git .

# The PicoLM repository keeps the C source/Makefile under picolm/.
WORKDIR /src/picolm
RUN make native
RUN test -x picolm

# ============================================================
# Stage 3: Download TinyLlama GGUF model
# ============================================================
FROM debian:bookworm-slim AS model-downloader

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /models
RUN curl -L --fail --retry 5 --retry-delay 3 --retry-all-errors \
    -o tinyllama-1.1b.chat-v1.0.Q4_K_M.gguf \
    "https://huggingface.co/nitsuai/TinyLlama-1.1B-Chat-v1.0-GGUF/resolve/main/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf" \
    && test -s tinyllama-1.1b.chat-v1.0.Q4_K_M.gguf

# ============================================================
# Stage 4: Runtime - WebUI + Gateway + local PicoLM adapter
# ============================================================
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Official PicoClaw binaries.
COPY --from=picoclaw-builder /src/picoclaw/build/picoclaw /app/picoclaw
COPY --from=picoclaw-builder /src/picoclaw/build/picoclaw-launcher /app/picoclaw-launcher

# Local PicoLM binary.
COPY --from=picolm-builder /src/picolm/picolm /app/picolm

# TinyLlama GGUF model (about 638 MB).
RUN mkdir -p /app/models
COPY --from=model-downloader /models/tinyllama-1.1b.chat-v1.0.Q4_K_M.gguf /app/models/tinyllama-1.1b.chat-v1.0.Q4_K_M.gguf

# Application config and local OpenAI-compatible adapter.
COPY config/ /config/
COPY scripts/ /app/scripts/
COPY entrypoint.sh /entrypoint.sh

RUN chmod +x /app/picoclaw /app/picoclaw-launcher /app/picolm /entrypoint.sh \
    && ln -sf /app/picoclaw /usr/local/bin/picoclaw \
    && ln -sf /app/picoclaw-launcher /usr/local/bin/picoclaw-launcher \
    && ln -sf /app/picolm /usr/local/bin/picolm

# Render supplies PORT at runtime. The launcher is the public listener;
# PicoClaw gateway and PicoLM adapter remain internal to the container.
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=5 \
    CMD-SHELL curl -fsS "http://127.0.0.1:${PORT:-8080}/" >/dev/null || exit 1

ENTRYPOINT ["/bin/sh", "/entrypoint.sh"]
