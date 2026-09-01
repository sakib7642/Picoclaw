#!/usr/bin/env python3
"""
PicoLM OpenAI-compatible HTTP API Adapter
Exposes an OpenAI-compatible REST API (/v1/models, /v1/chat/completions, /v1/completions)
wrapping the local PicoLM C inference engine and TinyLlama GGUF model.
"""

import json
import os
import subprocess
import sys
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import List, Dict, Any, Optional

# Configuration
HOST = os.environ.get("PICOLM_SERVER_HOST", "127.0.0.1")
PORT = int(os.environ.get("PICOLM_SERVER_PORT", "8000"))
DEFAULT_MODEL_NAME = "tinyllama-1.1b.chat-v1.0.Q4_K_M.gguf"

# Locate PicoLM binary
PICOLM_CANDIDATES = [
    os.environ.get("PICOLM_BIN_PATH", ""),
    "/app/picolm",
    "/usr/local/bin/picolm",
    "./picolm",
    "picolm",
]
PICOLM_BIN = next((p for p in PICOLM_CANDIDATES if p and os.path.exists(p)), "picolm")

# Locate GGUF Model
MODEL_CANDIDATES = [
    os.environ.get("PICOLM_MODEL_PATH", ""),
    "/app/models/tinyllama-1.1b.chat-v1.0.Q4_K_M.gguf",
    "/app/models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf",
    "./models/tinyllama-1.1b.chat-v1.0.Q4_K_M.gguf",
]
MODEL_PATH = next((p for p in MODEL_CANDIDATES if p and os.path.exists(p)), "/app/models/tinyllama-1.1b.chat-v1.0.Q4_K_M.gguf")

THREADS = int(os.environ.get("PICOLM_THREADS", "2"))


def format_chat_prompt(messages: List[Dict[str, Any]]) -> str:
    """Format messages into TinyLlama Chat template."""
    prompt = ""
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict) and "text" in item:
                    parts.append(item["text"])
                elif isinstance(item, str):
                    parts.append(item)
            content = " ".join(parts)
        elif not isinstance(content, str):
            content = str(content)

        if role == "system":
            prompt += f"<|system|>\n{content}</s>\n"
        elif role == "user":
            prompt += f"<|user|>\n{content}</s>\n"
        elif role == "assistant":
            prompt += f"<|assistant|>\n{content}</s>\n"
    prompt += "<|assistant|>\n"
    return prompt


def run_picolm(prompt: str, max_tokens: int = 256, temperature: float = 0.7, top_p: float = 0.9) -> str:
    """Invoke PicoLM binary and capture stdout output."""
    cmd = [
        PICOLM_BIN,
        MODEL_PATH,
        "-p", prompt,
        "-n", str(max_tokens),
        "-t", str(temperature),
        "-k", str(top_p),
        "-j", str(THREADS),
    ]

    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if res.returncode != 0:
            print(f"[picolm stderr]: {res.stderr}", file=sys.stderr)
        
        output = res.stdout.strip()
        # Clean up stop tokens if any
        for stop_tag in ["</s>", "<|im_end|>", "<|assistant|>", "<|user|>", "<|system|>"]:
            if stop_tag in output:
                output = output.split(stop_tag)[0].strip()
        return output
    except Exception as e:
        print(f"[picolm error]: {e}", file=sys.stderr)
        return f"Error executing inference: {e}"


class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Override to keep logs clean
        sys.stderr.write(f"[PicoLM-Server] {self.address_string()} - {format % args}\n")

    def _send_json(self, status_code: int, data: Any):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        if self.path in ("/", "/health"):
            self._send_json(200, {
                "status": "ok",
                "service": "picolm-server",
                "binary": PICOLM_BIN,
                "model": MODEL_PATH,
                "model_exists": os.path.exists(MODEL_PATH)
            })
        elif self.path.startswith("/v1/models"):
            models_data = [
                {"id": DEFAULT_MODEL_NAME, "object": "model", "created": int(time.time()), "owned_by": "picolm"},
                {"id": "tinyllama", "object": "model", "created": int(time.time()), "owned_by": "picolm"},
                {"id": "tinyllama-1.1b-chat", "object": "model", "created": int(time.time()), "owned_by": "picolm"}
            ]
            self._send_json(200, {"object": "list", "data": models_data})
        else:
            self._send_json(404, {"error": {"message": f"Endpoint {self.path} not found", "type": "invalid_request_error"}})

    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_len) if content_len > 0 else b"{}"
        
        try:
            body = json.loads(post_data.decode("utf-8"))
        except Exception as e:
            self._send_json(400, {"error": {"message": f"Malformed JSON: {e}", "type": "invalid_request_error"}})
            return

        if self.path == "/v1/chat/completions":
            self.handle_chat_completions(body)
        elif self.path == "/v1/completions":
            self.handle_completions(body)
        else:
            self._send_json(404, {"error": {"message": f"Endpoint {self.path} not found", "type": "invalid_request_error"}})

    def handle_chat_completions(self, body: Dict[str, Any]):
        messages = body.get("messages", [])
        model = body.get("model", DEFAULT_MODEL_NAME)
        max_tokens = int(body.get("max_tokens", 256))
        temperature = float(body.get("temperature", 0.7))
        top_p = float(body.get("top_p", 0.9))
        stream = bool(body.get("stream", False))

        prompt = format_chat_prompt(messages)
        created_time = int(time.time())
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            # Execute generation
            output_text = run_picolm(prompt, max_tokens, temperature, top_p)
            
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created_time,
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {"role": "assistant", "content": output_text},
                    "finish_reason": None
                }]
            }
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
            
            end_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created_time,
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop"
                }]
            }
            self.wfile.write(f"data: {json.dumps(end_chunk)}\n\n".encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
            output_text = run_picolm(prompt, max_tokens, temperature, top_p)
            prompt_tokens = len(prompt.split())
            completion_tokens = len(output_text.split())
            
            response = {
                "id": completion_id,
                "object": "chat.completion",
                "created": created_time,
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": output_text
                    },
                    "finish_reason": "stop"
                }],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens
                }
            }
            self._send_json(200, response)

    def handle_completions(self, body: Dict[str, Any]):
        prompt = body.get("prompt", "")
        if isinstance(prompt, list):
            prompt = " ".join(prompt)
        model = body.get("model", DEFAULT_MODEL_NAME)
        max_tokens = int(body.get("max_tokens", 256))
        temperature = float(body.get("temperature", 0.7))
        top_p = float(body.get("top_p", 0.9))

        output_text = run_picolm(prompt, max_tokens, temperature, top_p)
        created_time = int(time.time())
        completion_id = f"cmpl-{uuid.uuid4().hex[:12]}"
        prompt_tokens = len(prompt.split())
        completion_tokens = len(output_text.split())

        response = {
            "id": completion_id,
            "object": "text_completion",
            "created": created_time,
            "model": model,
            "choices": [{
                "text": output_text,
                "index": 0,
                "logprobs": None,
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens
            }
        }
        self._send_json(200, response)


def main():
    print(f"[PicoLM-Server] Starting OpenAI adapter on {HOST}:{PORT}")
    print(f"[PicoLM-Server] Binary: {PICOLM_BIN}")
    print(f"[PicoLM-Server] Model:  {MODEL_PATH} (exists: {os.path.exists(MODEL_PATH)})")
    server = HTTPServer((HOST, PORT), RequestHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[PicoLM-Server] Shutting down...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
