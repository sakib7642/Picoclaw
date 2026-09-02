#!/usr/bin/env python3
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

HOST = os.getenv('ROUTER_HOST', '127.0.0.1')
PORT = int(os.getenv('ROUTER_PORT', '8100'))
LOCAL_BASE = os.getenv('LOCAL_MODEL_BASE', 'http://127.0.0.1:8000/v1').rstrip('/')
LOCAL_MODEL_ID = 'mobilellm-350m'
REFRESH = int(os.getenv('ROUTER_REFRESH_SECONDS', '300'))
PROBE_EVERY = int(os.getenv('ROUTER_PROBE_SECONDS', '900'))
TIMEOUT = int(os.getenv('ROUTER_TIMEOUT_SECONDS', '120'))
QUOTA_COOLDOWN = int(os.getenv('ROUTER_QUOTA_COOLDOWN_SECONDS', '86400'))
STATE_FILE = os.getenv('ROUTER_STATE_FILE', '/root/.picoclaw/router-state.json')
ADMIN_PASSWORD = os.getenv('ROUTER_ADMIN_PASSWORD', os.getenv('PICOCLAW_WEBUI_PASSWORD', '')).strip()

# These are OpenAI-compatible providers. A provider is active only when its
# API-key environment variable exists and is non-empty. This keeps the image
# usable without leaking or inventing API keys. New providers can also be
# added through ROUTER_EXTRA_PROVIDERS_JSON or the local admin API.
BUILTIN_PROVIDERS = [
    ('openrouter', 'OPENROUTER_API_KEY', 'https://openrouter.ai/api/v1', 100),
    ('groq', 'GROQ_API_KEY', 'https://api.groq.com/openai/v1', 98),
    ('cerebras', 'CEREBRAS_API_KEY', 'https://api.cerebras.ai/v1', 97),
    ('gemini', 'GEMINI_API_KEY', 'https://generativelanguage.googleapis.com/v1beta/openai', 96),
    ('together', 'TOGETHER_API_KEY', 'https://api.together.xyz/v1', 94),
    ('fireworks', 'FIREWORKS_API_KEY', 'https://api.fireworks.ai/inference/v1', 92),
    ('xai', 'XAI_API_KEY', 'https://api.x.ai/v1', 90),
    ('mistral', 'MISTRAL_API_KEY', 'https://api.mistral.ai/v1', 88),
    ('deepseek', 'DEEPSEEK_API_KEY', 'https://api.deepseek.com/v1', 86),
    ('huggingface', 'HF_TOKEN', 'https://router.huggingface.co/v1', 84),
    ('cohere', 'COHERE_API_KEY', 'https://api.cohere.ai/compatibility/v1', 82),
    ('openai', 'OPENAI_API_KEY', 'https://api.openai.com/v1', 80),
]

BAD_MODEL_WORDS = (
    'embedding', 'embed', 'moderation', 'rerank', 'tts', 'speech', 'whisper',
    'image', 'vision-embedding', 'audio'
)

# Model quality is ranked dynamically from the provider's /models response.
# Bigger score = preferred. The router never hardcodes one provider's model.
TOP_PATTERNS = [
    (150, r'gpt-oss-120b|gpt-5|gpt-4\.1'),
    (149, r'claude.*sonnet|claude.*opus'),
    (148, r'gemini-3\.7-flash|gemini-3\.6-flash|gemini-3\.5-flash'),
    (147, r'kimi.*k2\.5|kimi.*k2'),
    (146, r'glm-5|glm-4\.7'),
    (145, r'qwen3\.6.*27b|qwen3\.5.*9b|qwen3.*235b|qwen.*72b'),
    (144, r'deepseek-v4|deepseek-r1|deepseek-chat'),
    (143, r'gemma-4.*27b|gemma-3.*27b'),
    (142, r'llama-4|llama-3\.3-70b'),
    (140, r'gpt-oss-20b|qwen3.*32b|qwen3.*30b'),
    (135, r'llama.*8b|qwen.*14b|qwen.*7b|command-a'),
]

state_lock = threading.RLock()
state = {
    'disabled_providers': [],
    'disabled_models': [],
    'custom_providers': [],
    'providers': {},
}
last_refresh = 0.0
last_probe = 0.0


def load_state():
    global state
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            state.update(loaded)
    except FileNotFoundError:
        pass
    except Exception as e:
        print('[Router] state load error:', e, flush=True)


def save_state():
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + '.tmp'
    with state_lock:
        data = {
            'disabled_providers': list(state.get('disabled_providers', [])),
            'disabled_models': list(state.get('disabled_models', [])),
            'custom_providers': list(state.get('custom_providers', [])),
            'providers': state.get('providers', {}),
        }
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def all_provider_defs():
    defs = list(BUILTIN_PROVIDERS)
    extra = os.getenv('ROUTER_EXTRA_PROVIDERS_JSON', '').strip()
    if extra:
        try:
            for p in json.loads(extra):
                if p.get('id') and p.get('api_key_env') and p.get('base_url'):
                    defs.append((p['id'], p['api_key_env'], p['base_url'].rstrip('/'), int(p.get('priority', 70))))
        except Exception as e:
            print('[Router] invalid ROUTER_EXTRA_PROVIDERS_JSON:', e, flush=True)
    for p in state.get('custom_providers', []):
        if p.get('id') and p.get('api_key_env') and p.get('base_url'):
            defs.append((p['id'], p['api_key_env'], p['base_url'].rstrip('/'), int(p.get('priority', 70))))
    # Last definition wins for custom overrides.
    merged = {}
    for item in defs:
        merged[item[0]] = item
    return list(merged.values())


def provider_defs():
    disabled = set(state.get('disabled_providers', []))
    out = []
    for pid, env, base, pri in all_provider_defs():
        key = os.getenv(env, '').strip()
        if key and pid not in disabled:
            out.append({'id': pid, 'env': env, 'base': base, 'priority': pri, 'key': key})
    return out


def score_model(mid, provider_priority):
    low = mid.lower()
    if any(w in low for w in BAD_MODEL_WORDS):
        return -100000
    score = provider_priority
    for points, pattern in TOP_PATTERNS:
        if re.search(pattern, low):
            score += points
            break
    if ':free' in low or low.endswith('-free') or ' free ' in low:
        score += 5
    if 'preview' in low or 'experimental' in low:
        score -= 2
    if 'instruct' in low or 'chat' in low:
        score += 3
    if 'base' in low and 'chat' not in low:
        score -= 8
    return score


def provider_state(pid):
    return state.setdefault('providers', {}).setdefault(pid, {
        'cooldown': 0.0,
        'models': {},
        'ok': False,
        'last_error': '',
        'last_refresh': 0.0,
    })


def parse_quota_reset(headers):
    # A 429 means the current quota bucket is treated as exhausted for 24h.
    # We deliberately do not retry sooner just because Retry-After is short.
    return QUOTA_COOLDOWN


def discover(p):
    ps = provider_state(p['id'])
    try:
        req = Request(
            p['base'] + '/models',
            headers={
                'Authorization': 'Bearer ' + p['key'],
                'User-Agent': 'PicoClaw-OmniRouter/2.0',
                'Accept': 'application/json',
            },
        )
        with urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode('utf-8', 'replace'))
        ids = [str(x['id']) for x in data.get('data', []) if isinstance(x, dict) and x.get('id')]
        now = time.time()
        with state_lock:
            ps['models'] = {
                mid: ps.get('models', {}).get(mid, {
                    'cooldown': 0.0,
                    'last_ok': 0.0,
                    'last_probe': 0.0,
                    'failures': 0,
                    'score': score_model(mid, p['priority']),
                }) for mid in ids
            }
            for mid, ms in ps['models'].items():
                ms['score'] = score_model(mid, p['priority'])
            ps['ok'] = True
            ps['last_error'] = ''
            ps['last_refresh'] = now
        return ids
    except HTTPError as e:
        body = e.read().decode('utf-8', 'replace')[:300]
        with state_lock:
            ps['ok'] = False
            ps['last_error'] = f'HTTP {e.code}: {body}'
            if e.code in (401, 403, 429):
                ps['cooldown'] = time.time() + QUOTA_COOLDOWN
        return []
    except Exception as e:
        with state_lock:
            ps['ok'] = False
            ps['last_error'] = str(e)[:300]
        return []


def refresh():
    global last_refresh
    ps = provider_defs()
    for p in ps:
        discover(p)
    last_refresh = time.time()
    active = ', '.join(p['id'] for p in ps) or 'none'
    print('[Router] registry refreshed; active providers: ' + active, flush=True)


def candidates():
    now = time.time()
    disabled_models = set(state.get('disabled_models', []))
    out = []
    for p in provider_defs():
        ps = provider_state(p['id'])
        if ps.get('cooldown', 0) > now:
            continue
        for mid, ms in dict(ps.get('models', {})).items():
            key = f'{p["id"]}/{mid}'
            if key in disabled_models or ms.get('cooldown', 0) > now or ms.get('score', -100000) < 0:
                continue
            penalty = min(ms.get('failures', 0) * 10, 50)
            out.append((ms.get('score', score_model(mid, p['priority'])) - penalty, p, mid))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def mark_failure(p, mid, status, headers, detail=''):
    now = time.time()
    if status == 429:
        cooldown = parse_quota_reset(headers)
    elif status in (401, 403):
        cooldown = QUOTA_COOLDOWN
    elif status in (400, 404, 422):
        cooldown = 6 * 3600
    elif status >= 500:
        cooldown = 120
    else:
        cooldown = 300
    with state_lock:
        ps = provider_state(p['id'])
        ms = ps['models'].setdefault(mid, {
            'cooldown': 0.0, 'last_ok': 0.0, 'last_probe': 0.0,
            'failures': 0, 'score': score_model(mid, p['priority']),
        })
        ms['failures'] = ms.get('failures', 0) + 1
        ms['cooldown'] = now + cooldown
        ps['last_error'] = f'HTTP {status}; cooldown={cooldown}s {detail}'.strip()
        if status in (401, 403, 429):
            ps['cooldown'] = max(ps.get('cooldown', 0), now + cooldown)
    save_state()


def mark_success(p, mid):
    with state_lock:
        ps = provider_state(p['id'])
        ms = ps['models'].setdefault(mid, {})
        ms['last_ok'] = time.time()
        ms['failures'] = max(0, ms.get('failures', 0) - 1)
        ms['cooldown'] = 0.0
        ps['cooldown'] = 0.0
        ps['ok'] = True
        ps['last_error'] = ''
    save_state()


def upstream(p, mid, payload):
    body = dict(payload)
    body['model'] = mid
    raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
    req = Request(
        p['base'] + '/chat/completions',
        data=raw,
        headers={
            'Authorization': 'Bearer ' + p['key'],
            'Content-Type': 'application/json',
            'Accept': 'text/event-stream, application/json',
            'User-Agent': 'PicoClaw-OmniRouter/2.0',
        },
        method='POST',
    )
    return urlopen(req, timeout=TIMEOUT)


def local_upstream(payload):
    body = dict(payload)
    body['model'] = LOCAL_MODEL_ID
    raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
    req = Request(
        LOCAL_BASE + '/chat/completions',
        data=raw,
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer local'},
        method='POST',
    )
    return urlopen(req, timeout=TIMEOUT)


def do_request(payload):
    global last_refresh
    if time.time() - last_refresh > REFRESH:
        refresh()
    errors = []
    for _, p, mid in candidates():
        try:
            r = upstream(p, mid, payload)
            return r, p['id'], mid, errors
        except HTTPError as e:
            detail = e.read().decode('utf-8', 'replace')[:200]
            mark_failure(p, mid, e.code, e.headers, detail)
            errors.append(f'{p["id"]}/{mid}: HTTP {e.code}')
        except (URLError, TimeoutError, OSError) as e:
            mark_failure(p, mid, 503, {}, str(e)[:120])
            errors.append(f'{p["id"]}/{mid}: {type(e).__name__}')
        except Exception as e:
            mark_failure(p, mid, 503, {}, str(e)[:120])
            errors.append(f'{p["id"]}/{mid}: {type(e).__name__}')
    try:
        return local_upstream(payload), 'local', LOCAL_MODEL_ID, errors
    except Exception as e:
        errors.append('local/' + LOCAL_MODEL_ID + ': ' + type(e).__name__)
    raise RuntimeError('No working model route: ' + '; '.join(errors[-10:]))


def auth_ok(handler):
    if not ADMIN_PASSWORD:
        return False
    value = handler.headers.get('Authorization', '')
    return value == 'Bearer ' + ADMIN_PASSWORD


class Handler(BaseHTTPRequestHandler):
    server_version = 'PicoClaw-OmniRouter/2.0'

    def log_message(self, fmt, *args):
        print('[Router] ' + fmt % args, flush=True)

    def _json(self, code, obj, extra_headers=None):
        raw = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        n = int(self.headers.get('Content-Length', '0'))
        return json.loads(self.rfile.read(n).decode('utf-8'))

    def do_GET(self):
        if self.path == '/health':
            cs = candidates()
            self._json(200, {
                'status': 'ok',
                'hosted_candidates': len(cs),
                'local_fallback': LOCAL_MODEL_ID,
                'last_refresh': last_refresh,
                'quota_cooldown_seconds': QUOTA_COOLDOWN,
            })
            return

        if self.path.startswith('/v1/models'):
            if time.time() - last_refresh > REFRESH:
                refresh()
            data = [{'id': 'omniroute', 'object': 'model', 'owned_by': 'picoclaw-router'}]
            for _, p, mid in candidates():
                data.append({'id': f'{p["id"]}/{mid}', 'object': 'model', 'owned_by': p['id']})
            data.append({'id': LOCAL_MODEL_ID, 'object': 'model', 'owned_by': 'facebook-mobilellm'})
            self._json(200, {'object': 'list', 'data': data})
            return

        if self.path == '/admin/status':
            if not auth_ok(self):
                self._json(401, {'error': 'unauthorized'})
                return
            with state_lock:
                snapshot = json.loads(json.dumps(state))
            for ps in snapshot.get('providers', {}).values():
                ps.pop('key', None)
            self._json(200, {
                'active_providers': [p['id'] for p in provider_defs()],
                'candidates': [
                    {'provider': p['id'], 'model': mid, 'score': score}
                    for score, p, mid in candidates()[:100]
                ],
                'state': snapshot,
            })
            return

        self._json(404, {'error': 'not_found'})

    def do_POST(self):
        if self.path not in ('/v1/chat/completions', '/chat/completions') and not self.path.startswith('/admin/'):
            self._json(404, {'error': 'not_found'})
            return

        if self.path.startswith('/admin/'):
            if not auth_ok(self):
                self._json(401, {'error': 'unauthorized'})
                return
            try:
                body = self._body()
                if self.path == '/admin/refresh':
                    refresh()
                    self._json(200, {'ok': True})
                    return

                if self.path == '/admin/provider':
                    action = body.get('action')
                    pid = str(body.get('id', '')).strip()
                    if not pid:
                        raise ValueError('provider id is required')
                    disabled = set(state.get('disabled_providers', []))
                    if action == 'disable':
                        disabled.add(pid)
                    elif action == 'enable':
                        disabled.discard(pid)
                    elif action == 'add':
                        env = str(body.get('api_key_env', '')).strip()
                        base = str(body.get('base_url', '')).strip().rstrip('/')
                        if not env or not base:
                            raise ValueError('api_key_env and base_url are required')
                        custom = [x for x in state.get('custom_providers', []) if x.get('id') != pid]
                        custom.append({'id': pid, 'api_key_env': env, 'base_url': base, 'priority': int(body.get('priority', 70))})
                        state['custom_providers'] = custom
                        disabled.discard(pid)
                    elif action == 'remove':
                        state['custom_providers'] = [x for x in state.get('custom_providers', []) if x.get('id') != pid]
                        disabled.add(pid)
                    else:
                        raise ValueError('action must be add/remove/enable/disable')
                    state['disabled_providers'] = sorted(disabled)
                    save_state()
                    refresh()
                    self._json(200, {'ok': True, 'providers': [p['id'] for p in provider_defs()]})
                    return

                if self.path == '/admin/model':
                    action = body.get('action')
                    provider = str(body.get('provider', '')).strip()
                    model = str(body.get('model', '')).strip()
                    key = provider + '/' + model
                    if not provider or not model:
                        raise ValueError('provider and model are required')
                    disabled = set(state.get('disabled_models', []))
                    if action == 'disable':
                        disabled.add(key)
                    elif action == 'enable':
                        disabled.discard(key)
                    elif action == 'remove':
                        disabled.add(key)
                    elif action == 'add':
                        disabled.discard(key)
                    else:
                        raise ValueError('action must be add/remove/enable/disable')
                    state['disabled_models'] = sorted(disabled)
                    save_state()
                    self._json(200, {'ok': True, 'model': key, 'disabled': key in disabled})
                    return

                self._json(404, {'error': 'unknown_admin_endpoint'})
            except Exception as e:
                self._json(400, {'error': str(e)})
            return

        try:
            payload = self._body()
            payload.pop('router_debug', None)
            r, pid, mid, _ = do_request(payload)
            if pid != 'local':
                p = next((p for p in provider_defs() if p['id'] == pid), None)
                if p:
                    mark_success(p, mid)
            self.send_response(r.status)
            for k, v in r.headers.items():
                if k.lower() in ('content-length', 'transfer-encoding', 'connection', 'server', 'date'):
                    continue
                self.send_header(k, v)
            self.send_header('X-PicoClaw-Route', pid + '/' + mid)
            self.end_headers()
            while True:
                chunk = r.read(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
            r.close()
            print(f'[Router] routed -> {pid}/{mid}', flush=True)
        except Exception as e:
            self._json(503, {'error': {'message': str(e), 'type': 'router_unavailable'}})


def probe_best_models():
    # One tiny real request per active provider periodically verifies that the
    # provider/model can actually answer. This is intentionally conservative so
    # health checking does not burn an entire free quota by itself.
    for p in provider_defs():
        ps = provider_state(p['id'])
        if ps.get('cooldown', 0) > time.time():
            continue
        available = [x for x in candidates() if x[1]['id'] == p['id']]
        if not available:
            continue
        _, _, mid = available[0]
        try:
            rr = upstream(p, mid, {
                'model': mid,
                'messages': [{'role': 'user', 'content': 'Reply with exactly OK'}],
                'max_tokens': 1,
                'temperature': 0,
                'stream': False,
            })
            rr.read(4096)
            rr.close()
            mark_success(p, mid)
            print(f'[Router] probe OK -> {p["id"]}/{mid}', flush=True)
        except HTTPError as e:
            detail = e.read().decode('utf-8', 'replace')[:160]
            mark_failure(p, mid, e.code, e.headers, detail)
            print(f'[Router] probe failed -> {p["id"]}/{mid}: HTTP {e.code}', flush=True)
        except Exception as e:
            mark_failure(p, mid, 503, {}, str(e)[:120])
            print(f'[Router] probe failed -> {p["id"]}/{mid}: {e}', flush=True)


def monitor():
    global last_probe
    while True:
        try:
            if time.time() - last_refresh > REFRESH:
                refresh()
            if time.time() - last_probe > PROBE_EVERY:
                probe_best_models()
                last_probe = time.time()
        except Exception as e:
            print('[Router] monitor error:', e, flush=True)
        time.sleep(30)


if __name__ == '__main__':
    load_state()
    refresh()
    threading.Thread(target=monitor, daemon=True).start()
    print(f'[Router] listening on {HOST}:{PORT}', flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
