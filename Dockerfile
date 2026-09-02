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
# Stage 3: Small MobileLLM local fallback
# The upstream GGUF repository is named MobileLLM-350M-GGUF, but
# its actual published model files are named MobileLLM-376M-*.
# Q4_K_S is ~262 MB and is the safest useful local fallback for
# Render Free's 512 MB RAM limit.
# ============================================================
FROM debian:bookworm-slim AS model-downloader

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /models

RUN curl -L --fail --retry 5 --retry-delay 3 --retry-all-errors \
    -o MobileLLM-376M-Q4_K_S.gguf \
    "https://huggingface.co/pjh64/MobileLLM-350M-GGUF/resolve/main/MobileLLM-376M-Q4_K_S.gguf" \
    && test -s MobileLLM-376M-Q4_K_S.gguf

# ============================================================
# Stage 4: Runtime - official PicoClaw WebUI + smart router
# ============================================================
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tzdata libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV LD_LIBRARY_PATH="/app/llama-bin"

COPY --from=picoclaw-builder /src/picoclaw/build/picoclaw /app/picoclaw
COPY --from=picoclaw-builder /src/picoclaw/build/picoclaw-launcher /app/picoclaw-launcher
# llama.cpp's server is dynamically linked; copy its companion .so files too.
COPY --from=llama-builder /src/llama.cpp/build/bin/ /app/llama-bin/
COPY --from=model-downloader /models/MobileLLM-376M-Q4_K_S.gguf /app/models/MobileLLM-376M-Q4_K_S.gguf

COPY config/ /config/
COPY scripts/ /app/scripts/
COPY entrypoint.sh /entrypoint.sh

RUN chmod +x /app/picoclaw /app/picoclaw-launcher /app/llama-bin/llama-server \
        /app/scripts/model_supervisor.sh /app/scripts/router_supervisor.sh \
        /app/scripts/auth_bootstrap.sh /app/scripts/model_router.py /entrypoint.sh \
    && ln -sf /app/picoclaw /usr/local/bin/picoclaw \
    && ln -sf /app/picoclaw-launcher /usr/local/bin/picoclaw-launcher \
    && ln -sf /app/llama-bin/llama-server /usr/local/bin/llama-server \
    && /app/llama-bin/llama-server --version

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 \
    CMD /bin/sh -c 'curl -fsS "http://127.0.0.1:${PORT:-8080}/" >/dev/null || exit 1'

ENTRYPOINT ["/bin/sh", "/entrypoint.sh"]
