# PicoClaw + local PicoLM + TinyLlama

All-in-one Docker deployment of the official PicoClaw WebUI/Launcher with a local PicoLM inference adapter and TinyLlama 1.1B Q4_K_M.

## Architecture

```text
Internet
   │
Render $PORT
   │
PicoClaw WebUI / Launcher  (public)
   │
   └── PicoClaw Gateway    127.0.0.1:18790
           │
           └── OpenAI-compatible PicoLM adapter 127.0.0.1:8000
                    │
                    └── /app/picolm + TinyLlama GGUF
```

The upstream PicoClaw WebUI is kept intact. The only custom integration is the local OpenAI-compatible endpoint used by the PicoClaw model configuration.

## Important details

- PicoClaw is pinned to **v0.3.1** for a reproducible WebUI build.
- The v0.3.1 build uses **Go 1.25.11+**, Node.js 22+, and pnpm 10.33.0+.
- The public listener is the **WebUI launcher**, not the PicoClaw gateway. This avoids the previous `404 page not found` result from exposing the gateway root.
- The gateway stays internal on `127.0.0.1:18790`.
- The local PicoLM adapter stays internal on `127.0.0.1:8000`.
- Telegram is intentionally not configured in the repository. It can be added later from the WebUI, so no Telegram token is required for this deployment.
- Render supplies the public `PORT`; the entrypoint passes it to the launcher.

## Repository structure

```text
.
├── Dockerfile
├── entrypoint.sh
├── config/
│   └── config.json
├── scripts/
│   └── picolm_server.py
├── .dockerignore
└── README.md
```

## Local model

The image bundles:

```text
tinyllama-1.1b.chat-v1.0.Q4_K_M.gguf
```

The model is downloaded during the Docker build and served through the local adapter at:

```text
http://127.0.0.1:8000/v1
```

PicoClaw uses its normal `model_list` / OpenAI-compatible provider configuration, so the WebUI can continue to manage the normal PicoClaw configuration surface.

## Render deployment

Create a **Web Service** from this repository using the **Docker** runtime and the repository's root `Dockerfile`.

The container starts the local adapter first and then launches the official PicoClaw WebUI. The launcher starts/manages the gateway using `/root/.picoclaw/config.json`.

For a fresh container, the initial configuration is copied from `config/config.json`; after startup, the WebUI is the intended configuration interface.

## Verification

From inside the container the expected services are:

- WebUI: `0.0.0.0:$PORT`
- Gateway: `127.0.0.1:18790`
- PicoLM adapter: `127.0.0.1:8000`

The Docker health check probes the WebUI root path. Render should therefore detect the public WebUI listener rather than the internal gateway.
