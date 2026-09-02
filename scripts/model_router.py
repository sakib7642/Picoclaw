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
LOCAL_MODEL_ID = os.getenv('LOCAL_MODEL_ID', 'mobilellm-376m')
REFRESH = int(os.getenv('ROUTER_REFRESH_SECONDS', '300'))
PROBE_EVERY = int(os.getenv('ROUTER_PROBE_SECONDS', '900'))
TIMEOUT = int(os.getenv('ROUTER_TIMEOUT_SECONDS', '120'))
QUOTA_COOLDOWN = int(os.getenv('ROUTER_QUOTA_COOLDOWN_SECONDS', '86400'))
STATE_FILE = os.getenv('ROUTER_STATE_FILE', '/root/.picoclaw/router-state.json')
ADMIN_PASSWORD = os.getenv('ROUTER_ADMIN_PASSWORD', os.getenv('PICOCLAW_WEBUI_PASSWORD', '')).strip()

# Provider adapters are enabled only when their API-key environment variable
# is present. Additional OpenAI-compatible providers can be added at runtime.
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
BAD = ('embedding', 'embed', 'moderation', 'rerank', 'tts', 'speech', 'whisper', 'audio')
PATTERNS = [
    (150, r'gpt-oss-120b|gpt-5|gpt-4\.1'),
    (149, r'claude.*sonnet|claude.*opus'),
    (148, r'gemini-3.*flash'),
    (147, r'kimi.*k2'),
    (146, r'glm-5|glm-4\.7'),
    (145, r'qwen3.*(235b|72b|32b|30b|27b)|qwen3\.5.*9b'),
    (144, r'deepseek-v4|deepseek-r1|deepseek-chat'),
    (143, r'gemma-4.*27b|gemma-3.*27b'),
    (142, r'llama-4|llama-3\.3-70b'),
    (135, r'llama.*8b|qwen.*14b|qwen.*7b|command-a'),
]

lock = threading.RLock()
state = {'disabled_providers': [], 'disabled_models': [], 'custom_providers': [], 'providers': {}}
last_refresh = 0.0
last_probe = 0.0


def load_state():
    global state
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            value = json.load(f)
        if isinstance(value, dict):
            state.update(value)
    except FileNotFoundError:
        pass
    except Exception as e:
        print('[Router] state load error:', e, flush=True)


def save_state():
    directory = os.path.dirname(STATE_FILE)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = STATE_FILE + '.tmp'
    with lock:
        value = {k: state.get(k, []) for k in ('disabled_providers', 'disabled_models', 'custom_providers')}
        value['providers'] = state.get('providers', {})
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def provider_defs():
    defs = list(BUILTIN_PROVIDERS)
    extra = os.getenv('ROUTER_EXTRA_PROVIDERS_JSON', '').strip()
    if extra:
        try:
            for p in json.loads(extra):
                if p.get('id') and p.get('api_key_env') and p.get('base_url'):
                    defs.append((p['id'], p['api_key_env'], p['base_url'].rstrip('/'), int(p.get('priority', 70))))
        except Exception as e:
            print('[Router] invalid extra providers:', e, flush=True)
    for p in state.get('custom_providers', []):
        if p.get('id') and p.get('api_key_env') and p.get('base_url'):
            defs.append((p['id'], p['api_key_env'], p['base_url'].rstrip('/'), int(p.get('priority', 70))))
    merged = {}
    for pid, env, base, pri in defs:
        merged[pid] = (pid, env, base, pri)
    disabled = set(state.get('disabled_providers', []))
    return [
        {'id': pid, 'env': env, 'base': base, 'priority': pri, 'key': os.getenv(env, '').strip()}
        for pid, env, base, pri in merged.values()
        if pid not in disabled and os.getenv(env, '').strip()
    ]


def pstate(pid):
    return state.setdefault('providers', {}).setdefault(pid, {'cooldown': 0, 'models': {}, 'ok': False, 'last_error': ''})


def score(mid, pri):
    low = mid.lower()
    if any(x in low for x in BAD):
        return -100000
    value = pri
    for points, pattern in PATTERNS:
        if re.search(pattern, low):
            value += points
            break
    if ':free' in low or low.endswith('-free'):
        value += 5
    if 'preview' in low or 'experimental' in low:
        value -= 2
    if 'instruct' in low or 'chat' in low:
        value += 3
    return value


def discover(p):
    ps = pstate(p['id'])
    try:
        req = Request(p['base'] + '/models', headers={'Authorization': 'Bearer ' + p['key'], 'Accept': 'application/json', 'User-Agent': 'PicoClaw-OmniRouter/2.2'})
        with urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode('utf-8', 'replace'))
        ids = [str(x['id']) for x in data.get('data', []) if isinstance(x, dict) and x.get('id')]
        old = ps.get('models', {})
        ps['models'] = {mid: old.get(mid, {'cooldown': 0, 'failures': 0, 'last_ok': 0, 'score': score(mid, p['priority'])}) for mid in ids}
        for mid in ids:
            ps['models'][mid]['score'] = score(mid, p['priority'])
        ps['ok'] = True
        ps['last_error'] = ''
        ps['last_refresh'] = time.time()
    except HTTPError as e:
        body = e.read().decode('utf-8', 'replace')[:240]
        ps['ok'] = False
        ps['last_error'] = f'HTTP {e.code}: {body}'
        if e.code in (401, 403, 429):
            ps['cooldown'] = time.time() + QUOTA_COOLDOWN
    except Exception as e:
        ps['ok'] = False
        ps['last_error'] = str(e)[:240]


def refresh():
    global last_refresh
    for p in provider_defs():
        discover(p)
    last_refresh = time.time()
    print('[Router] active providers: ' + ', '.join(p['id'] for p in provider_defs()) if provider_defs() else '[Router] active providers: none', flush=True)


def candidates():
    now = time.time()
    disabled = set(state.get('disabled_models', []))
    out = []
    for p in provider_defs():
        ps = pstate(p['id'])
        if ps.get('cooldown', 0) > now:
            continue
        for mid, ms in ps.get('models', {}).items():
            if f'{p["id"]}/{mid}' in disabled or ms.get('cooldown', 0) > now or ms.get('score', -100000) < 0:
                continue
            out.append((ms.get('score', score(mid, p['priority'])) - min(ms.get('failures', 0) * 10, 50), p, mid))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def fail(p, mid, status, detail=''):
    now = time.time()
    cooldown = QUOTA_COOLDOWN if status in (401, 403, 429) else 6 * 3600 if status in (400, 404, 422) else 120 if status >= 500 else 300
    ps = pstate(p['id'])
    ms = ps['models'].setdefault(mid, {'cooldown': 0, 'failures': 0, 'last_ok': 0, 'score': score(mid, p['priority'])})
    ms['failures'] = ms.get('failures', 0) + 1
    ms['cooldown'] = now + cooldown
    ps['last_error'] = f'HTTP {status}: {detail}'[:300]
    if status in (401, 403, 429):
        ps['cooldown'] = now + cooldown
    save_state()


def success(p, mid):
    ps = pstate(p['id'])
    ms = ps['models'].setdefault(mid, {})
    ms['last_ok'] = time.time()
    ms['failures'] = max(0, ms.get('failures', 0) - 1)
    ms['cooldown'] = 0
    ps['cooldown'] = 0
    ps['ok'] = True
    ps['last_error'] = ''
    save_state()


def post_chat(base, key, model, payload):
    body = dict(payload)
    body['model'] = model
    raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
    req = Request(base + '/chat/completions', data=raw, method='POST', headers={
        'Authorization': 'Bearer ' + key,
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream, application/json',
        'User-Agent': 'PicoClaw-OmniRouter/2.2',
    })
    return urlopen(req, timeout=TIMEOUT)


def route(payload):
    global last_refresh
    if time.time() - last_refresh > REFRESH:
        refresh()
    errors = []
    for _, p, mid in candidates():
        try:
            return post_chat(p['base'], p['key'], mid, payload), p['id'], mid
        except HTTPError as e:
            detail = e.read().decode('utf-8', 'replace')[:180]
            fail(p, mid, e.code, detail)
            errors.append(f'{p["id"]}/{mid}: HTTP {e.code}')
        except (URLError, TimeoutError, OSError) as e:
            fail(p, mid, 503, str(e)[:100])
            errors.append(f'{p["id"]}/{mid}: network')
        except Exception as e:
            fail(p, mid, 503, str(e)[:100])
            errors.append(f'{p["id"]}/{mid}: error')
    try:
        return post_chat(LOCAL_BASE, 'local', LOCAL_MODEL_ID, payload), 'local', LOCAL_MODEL_ID
    except Exception as e:
        errors.append('local/' + LOCAL_MODEL_ID + ': ' + str(e)[:100])
    raise RuntimeError('No working route: ' + '; '.join(errors[-10:]))


def admin_ok(handler):
    return bool(ADMIN_PASSWORD) and handler.headers.get('Authorization', '') == 'Bearer ' + ADMIN_PASSWORD


class Handler(BaseHTTPRequestHandler):
    server_version = 'PicoClaw-OmniRouter/2.2'

    def log_message(self, fmt, *args):
        print('[Router] ' + fmt % args, flush=True)

    def send_json(self, code, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def body(self):
        n = int(self.headers.get('Content-Length', '0'))
        return json.loads(self.rfile.read(n).decode('utf-8'))

    def do_GET(self):
        if self.path == '/health':
            self.send_json(200, {'status': 'ok', 'hosted_candidates': len(candidates()), 'local_fallback': LOCAL_MODEL_ID})
            return
        if self.path.startswith('/v1/models'):
            if time.time() - last_refresh > REFRESH:
                refresh()
            data = [{'id': 'omniroute', 'object': 'model', 'owned_by': 'picoclaw-router'}]
            data += [{'id': f'{p["id"]}/{mid}', 'object': 'model', 'owned_by': p['id']} for _, p, mid in candidates()]
            data.append({'id': LOCAL_MODEL_ID, 'object': 'model', 'owned_by': 'mobilellm'})
            self.send_json(200, {'object': 'list', 'data': data})
            return
        if self.path == '/admin/status':
            if not admin_ok(self):
                self.send_json(401, {'error': 'unauthorized'})
                return
            with lock:
                snapshot = json.loads(json.dumps(state))
            self.send_json(200, {'active_providers': [p['id'] for p in provider_defs()], 'candidates': [{'provider': p['id'], 'model': mid, 'score': s} for s, p, mid in candidates()[:100]], 'state': snapshot})
            return
        self.send_json(404, {'error': 'not_found'})

    def do_POST(self):
        if self.path.startswith('/admin/'):
            if not admin_ok(self):
                self.send_json(401, {'error': 'unauthorized'})
                return
            try:
                b = self.body()
                if self.path == '/admin/refresh':
                    refresh(); self.send_json(200, {'ok': True}); return
                if self.path == '/admin/provider':
                    action, pid = b.get('action'), str(b.get('id', '')).strip()
                    if not pid: raise ValueError('provider id is required')
                    disabled = set(state.get('disabled_providers', []))
                    if action == 'disable': disabled.add(pid)
                    elif action == 'enable': disabled.discard(pid)
                    elif action == 'add':
                        env, base = str(b.get('api_key_env', '')).strip(), str(b.get('base_url', '')).strip().rstrip('/')
                        if not env or not base: raise ValueError('api_key_env and base_url are required')
                        state['custom_providers'] = [x for x in state.get('custom_providers', []) if x.get('id') != pid]
                        state['custom_providers'].append({'id': pid, 'api_key_env': env, 'base_url': base, 'priority': int(b.get('priority', 70))})
                        disabled.discard(pid)
                    elif action == 'remove':
                        state['custom_providers'] = [x for x in state.get('custom_providers', []) if x.get('id') != pid]; disabled.add(pid)
                    else: raise ValueError('action must be add/remove/enable/disable')
                    state['disabled_providers'] = sorted(disabled); save_state(); refresh()
                    self.send_json(200, {'ok': True}); return
                if self.path == '/admin/model':
                    action, provider, model = b.get('action'), str(b.get('provider', '')).strip(), str(b.get('model', '')).strip()
                    if not provider or not model: raise ValueError('provider and model are required')
                    key, disabled = provider + '/' + model, set(state.get('disabled_models', []))
                    if action in ('disable', 'remove'): disabled.add(key)
                    elif action in ('enable', 'add'): disabled.discard(key)
                    else: raise ValueError('action must be add/remove/enable/disable')
                    state['disabled_models'] = sorted(disabled); save_state(); self.send_json(200, {'ok': True, 'disabled': key in disabled}); return
                self.send_json(404, {'error': 'unknown_admin_endpoint'})
            except Exception as e:
                self.send_json(400, {'error': str(e)})
            return

        if self.path not in ('/v1/chat/completions', '/chat/completions'):
            self.send_json(404, {'error': 'not_found'}); return
        try:
            payload = self.body()
            payload.pop('router_debug', None)
            r, pid, mid = route(payload)
            if pid != 'local':
                p = next((x for x in provider_defs() if x['id'] == pid), None)
                if p: success(p, mid)
            self.send_response(r.status)
            for k, v in r.headers.items():
                if k.lower() in ('content-length', 'transfer-encoding', 'connection', 'server', 'date'):
                    continue
                self.send_header(k, v)
            self.send_header('X-PicoClaw-Route', pid + '/' + mid)
            self.end_headers()
            while True:
                chunk = r.read(8192)
                if not chunk: break
                self.wfile.write(chunk); self.wfile.flush()
            r.close()
            print('[Router] routed -> ' + pid + '/' + mid, flush=True)
        except Exception as e:
            self.send_json(503, {'error': {'message': str(e), 'type': 'router_unavailable'}})


def probe():
    for p in provider_defs():
        if pstate(p['id']).get('cooldown', 0) > time.time():
            continue
        cs = [x for x in candidates() if x[1]['id'] == p['id']]
        if not cs: continue
        _, _, mid = cs[0]
        try:
            r = post_chat(p['base'], p['key'], mid, {'messages': [{'role': 'user', 'content': 'OK'}], 'max_tokens': 1, 'temperature': 0, 'stream': False})
            r.read(2048); r.close(); success(p, mid)
            print('[Router] probe OK -> ' + p['id'] + '/' + mid, flush=True)
        except HTTPError as e:
            fail(p, mid, e.code, e.read().decode('utf-8', 'replace')[:120])
        except Exception as e:
            fail(p, mid, 503, str(e)[:100])


def monitor():
    global last_probe
    while True:
        try:
            if time.time() - last_refresh > REFRESH: refresh()
            if time.time() - last_probe > PROBE_EVERY:
                probe(); last_probe = time.time()
        except Exception as e:
            print('[Router] monitor error:', e, flush=True)
        time.sleep(30)


if __name__ == '__main__':
    load_state()
    refresh()
    threading.Thread(target=monitor, daemon=True).start()
    print(f'[Router] listening on {HOST}:{PORT}', flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
