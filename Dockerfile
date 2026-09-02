# ============================================================
# Stage 1: Build the latest official PicoClaw + WebUI launcher
# ============================================================
FROM node:22.20-bookworm AS picoclaw-builder

ARG GO_VERSION=1.25.11

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

RUN npm install -g pnpm@10.33.0 \
    && pnpm --version \
    && go version

WORKDIR /src/picoclaw
# No version pin: every fresh Render build uses the current official main branch.
RUN git clone --depth 1 https://github.com/sipeed/picoclaw.git .

RUN cd web/frontend \
    && pnpm install --frozen-lockfile

RUN make build
RUN make build-launcher
RUN test -x build/picoclaw && test -x build/picoclaw-launcher

# ============================================================
# Stage 2: llama.cpp CPU server (OpenAI-compatible)
# ============================================================
FROM debian:bookworm-slim AS llama-builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tar grep \
    && rm -rf /var/lib/apt/lists/*

ARG LLAMA_TAG=b10695
ENV LD_LIBRARY_PATH="/opt/llama:/opt/llama/lib:/opt/llama/bin"

RUN mkdir -p /opt/llama \
    && curl -fsSL --retry 5 --retry-delay 3 --retry-all-errors \
       "https://github.com/ggml-org/llama.cpp/releases/download/${LLAMA_TAG}/llama-${LLAMA_TAG}-bin-ubuntu-x64.tar.gz" \
       -o /tmp/llama.tgz \
    && tar -xzf /tmp/llama.tgz -C /opt/llama \
    && rm -f /tmp/llama.tgz \
    && LLAMA_SERVER="$(find /opt/llama -type f -name llama-server -print -quit)" \
    && test -n "${LLAMA_SERVER}" \
    && cp "${LLAMA_SERVER}" /opt/llama-server \
    && find /opt/llama -mindepth 2 -type f -name '*.so*' -exec cp -a {} /opt/llama/ \; \
    && chmod +x /opt/llama-server

RUN LD_LIBRARY_PATH="/opt/llama:/opt/llama/lib:/opt/llama/bin" /opt/llama-server --version

# ============================================================
# Stage 3: Qwen3.5-0.8B Q4_K_M GGUF
# ============================================================
FROM debian:bookworm-slim AS model-downloader

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /models
RUN curl -L --fail --retry 5 --retry-delay 3 --retry-all-errors \
    -o Qwen3.5-0.8B.Q4_K_M.gguf \
    "https://huggingface.co/mradermacher/Qwen3.5-0.8B-GGUF/resolve/main/Qwen3.5-0.8B.Q4_K_M.gguf" \
    && test -s Qwen3.5-0.8B.Q4_K_M.gguf

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
COPY --from=llama-builder /opt/llama/ /opt/llama/
COPY --from=llama-builder /opt/llama-server /app/llama-server

RUN mkdir -p /app/models
COPY --from=model-downloader /models/Qwen3.5-0.8B.Q4_K_M.gguf /app/models/Qwen3.5-0.8B.Q4_K_M.gguf

COPY config/ /config/
COPY scripts/ /app/scripts/
COPY entrypoint.sh /entrypoint.sh

RUN chmod +x /app/picoclaw /app/picoclaw-launcher /app/llama-server /app/scripts/model_supervisor.sh /entrypoint.sh \
    && ln -sf /app/picoclaw /usr/local/bin/picoclaw \
    && ln -sf /app/picoclaw-launcher /usr/local/bin/picoclaw-launcher

ENV LD_LIBRARY_PATH="/opt/llama:/opt/llama/lib:/opt/llama/bin"

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=5 \
    CMD /bin/sh -c 'curl -fsS "http://127.0.0.1:${PORT:-8080}/" >/dev/null || exit 1'

ENTRYPOINT ["/bin/sh", "/entrypoint.sh"]
