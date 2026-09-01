# PicoClaw + Local PicoLM Stack

An all-in-one, fully offline, self-hosted deployment of [PicoClaw](https://github.com/sipeed/picoclaw) powered by a local [PicoLM](https://github.com/RightNow-AI/picolm) inference engine and the TinyLlama 1.1B Q4_K_M GGUF model running inside a single container on Render.

## Architecture

```
Internet
   │
Render
   │
PicoClaw (:8080)
   │
Local OpenAI Adapter (:8000)
   │
PicoLM (C inference engine)
   │
TinyLlama 1.1B Q4_K_M GGUF
```

- **PicoClaw**: Lightweight AI agent runtime written in Go (v0.3.1) listening on `0.0.0.0:8080`.
- **PicoLM Adapter**: Lightweight OpenAI-compatible Python HTTP server running on `127.0.0.1:8000`.
- **PicoLM**: Ultra-lightweight pure C inference engine executing inference locally.
- **TinyLlama 1.1B**: Quantized (Q4_K_M) GGUF model bundled directly in `/app/models/`.

## Repository Structure

```
.
├── Dockerfile              # Multi-stage Docker build
├── entrypoint.sh           # Container init, health checks & supervisor
├── config/
│   └── config.json         # PicoClaw configuration (local OpenAI provider)
├── scripts/
│   └── picolm_server.py    # OpenAI-compatible /v1 adapter for PicoLM
├── .dockerignore
└── README.md
```

## Deployment on Render

1. Connect the GitHub repository `sakib7642/Picoclaw` to Render.
2. Create a new **Web Service** with:
   - **Runtime**: Docker
   - **Region**: Singapore (or Oregon)
   - **Plan**: Free (or Starter)
3. PicoClaw binds to `0.0.0.0:8080` (or the dynamic `$PORT` provided by Render).
4. Access the service at your Render URL (e.g., `https://<service-name>.onrender.com`).

## Verification & Health Endpoints

- **PicoClaw Health Check**: `GET /health` (Port `8080`)
- **Internal Adapter Health**: `GET /health` or `GET /v1/models` (Port `8000`)