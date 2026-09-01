# ============================================================
# Stage 1: Build PicoClaw Go binary and WebUI
# ============================================================
FROM node:22-alpine AS picoclaw-builder

RUN apk add --no-cache git make gcc musl-dev go
RUN npm install -g pnpm

WORKDIR /src
RUN git clone --depth 1 --branch v0.3.1 https://github.com/sipeed/picoclaw.git .

# Build main binary
RUN mkdir -p /app && \
    CGO_ENABLED=0 go build -tags "goolm,stdjson" -ldflags "-s -w" -o /app/picoclaw ./cmd/picoclaw

# Build Launcher/WebUI
WORKDIR /src/web/frontend
RUN pnpm install && pnpm run build:backend
WORKDIR /src/web
# Skip pnpm install in make by already having built in backend/dist
RUN make build

# Copy launcher
RUN cp build/picoclaw-launcher /app/picoclaw-launcher

# ============================================================
# Stage 2: Build PicoLM C binary
# ============================================================
FROM alpine:3.21 AS picolm-builder

RUN apk add --no-cache git gcc musl-dev make

WORKDIR /src
RUN git clone --depth 1 https://github.com/RightNow-AI/picolm.git .
WORKDIR /src/picolm
RUN mkdir -p /app && \
    gcc -O2 -std=c11 -D_GNU_SOURCE -Wall -Wextra -Wpedantic -o /app/picolm \
    picolm.c model.c tensor.c quant.c tokenizer.c sampler.c grammar.c -lm -lpthread

# ============================================================
# Stage 3: Download TinyLlama GGUF Model
# ============================================================
FROM alpine:3.21 AS model-downloader

RUN apk add --no-cache curl

WORKDIR /models
RUN curl -L -f -o tinyllama-1.1b.chat-v1.0.Q4_K_M.gguf \
    "https://huggingface.co/TheBloke/TinyLlama-1.1B-Chat-v1.0-GGUF/resolve/main/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf" && \
    [ -s tinyllama-1.1b.chat-v1.0.Q4_K_M.gguf ]

# ============================================================
# Stage 4: Final Runtime Container
# ============================================================
FROM python:3.12-alpine

RUN apk add --no-cache ca-certificates tzdata curl libgcc libgomp bash

WORKDIR /app

# Binaries
COPY --from=picoclaw-builder /app/picoclaw /app/picoclaw
COPY --from=picoclaw-builder /app/picoclaw-launcher /app/picoclaw-launcher
COPY --from=picoclaw-builder /app/picoclaw /usr/local/bin/picoclaw
COPY --from=picoclaw-builder /app/picoclaw-launcher /usr/local/bin/picoclaw-launcher
COPY --from=picolm-builder /app/picolm /app/picolm
COPY --from=picolm-builder /app/picolm /usr/local/bin/picolm

# Model
RUN mkdir -p /app/models
COPY --from=model-downloader /models/tinyllama-1.1b.chat-v1.0.Q4_K_M.gguf /app/models/tinyllama-1.1b.chat-v1.0.Q4_K_M.gguf
RUN ln -sf /app/models/tinyllama-1.1b.chat-v1.0.Q4_K_M.gguf /app/models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf

# Scripts & Config
COPY scripts/ /app/scripts/
COPY config/ /config/
COPY config/ /app/config/
COPY entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh /app/picoclaw /app/picolm /app/picoclaw-launcher /usr/local/bin/picoclaw /usr/local/bin/picolm /usr/local/bin/picoclaw-launcher

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

ENTRYPOINT ["/bin/sh", "/entrypoint.sh"]
