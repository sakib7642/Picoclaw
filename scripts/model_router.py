#!/usr/bin/env python3
import json, os, re, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

HOST = os.getenv('ROUTER_HOST', '127.0.0.1')
PORT = int(os.getenv('ROUTER_PORT', '8100'))
LOCAL_BASE = os.getenv('LOCAL_MODEL_BASE', 'http://127.0.0.1:8000/v1').rstrip('/')
REFRESH = int(os.getenv('ROUTER_REFRESH_SECONDS', '300'))
PROBE = os.getenv('ROUTER_PROBE_ENABLED', '1').lower() not in ('0', 'false', 'no')
PROBE_EVERY = int(os.getenv('ROUTER_PROBE_SECONDS', '900'))
TIMEOUT = int(os.getenv('ROUTER_TIMEOUT_SECONDS', '120'))

PROVIDERS = [
    ('openrouter', 'OPENROUTER_API_KEY', 'https://openrouter.ai/api/v1', 100),
    ('xai', 'XAI_API_KEY', 'https://api.x.ai/v1', 98),
    ('gemini', 'GEMINI_API_KEY', 'https://generativelanguage.googleapis.com/v1beta/openai', 96),
    ('groq', 'GROQ_API_KEY', 'https://api.groq.com/openai/v1', 94),
    ('cerebras', 'CEREBRAS_API_KEY', 'https://api.cerebras.ai/v1', 92),
    ('together', 'TOGETHER_API_KEY', 'https://api.together.xyz/v1', 90),
    ('huggingface', 'HF_TOKEN', 'https://router.huggingface.co/v1', 88),
    ('mistral', 'MISTRAL_API_KEY', 'https://api.mistral.ai/v1', 86),
    ('deepseek', 'DEEPSEEK_API_KEY', 'https://api.deepseek.com/v1', 84),
    ('fireworks', 'FIREWORKS_API_KEY', 'https://api.fireworks.ai/inference/v1', 82),
    ('openai', 'OPENAI_API_KEY', 'https://api.openai.com/v1', 80),
]

EXTRA = os.getenv('ROUTER_EXTRA_PROVIDERS_JSON', '').strip()
if EXTRA:
    try:
        for p in json.loads(EXTRA):
            if p.get('id') and p.get('api_key_env') and p.get('base_url'):
                PROVIDERS.append((p['id'], p['api_key_env'], p['base_url'].rstrip('/'), int(p.get('priority', 70))))
    except Exception as e:
        print(f'[Router] invalid ROUTER_EXTRA_PROVIDERS_JSON: {e}', flush=True)

BAD_MODEL_WORDS = ('embedding', 'embed', 'moderation', 'guard', 'rerank', 'tts', 'speech', 'whisper', 'image')
TOP_PATTERNS = [
    (120, r'gpt-oss-120b'), (119, r'kimi.?k2\.5'), (118, r'glm-5'),
    (117, r'gemini-3\.7-flash'), (116, r'gemini-3\.6-flash'),
    (115, r'gemini-3\.5-flash'), (114, r'gpt-4\.1'), (113, r'claude-3\.7|claude-4|claude-sonnet'),
    (112, r'qwen3\.6-27b|qwen3\.5.*9b'), (111, r'deepseek-v4|deepseek-r1'),
    (110, r'mistral-large|mistral-medium'), (108, r'gemma-4.*31b'),
    (105, r'gpt-oss-20b'), (104, r'llama-4|llama-3\.3-70b'), (102, r'qwen3.*32b|qwen3.*30b'),
    (100, r'gemma-3.*27b'), (96, r'llama.*8b|qwen.*14b|qwen.*7b'),
]

state_lock = threading.RLock()
state = {}
last_refresh = 0.0
last_probe = 0.0


def provider_defs():
    out = []
    for pid, env, base, pri in PROVIDERS:
        key = os.getenv(env, '').strip()
        if key:
            out.append({'id': pid, 'env': env, 'base': base, 'priority': pri, 'key': key})
    return out


def score_model(mid, provider_priority):
    s = provider_priority
    low = mid.lower()
    if any(w in low for w in BAD_MODEL_WORDS):
        return -10000
    for pts, pat in TOP_PATTERNS:
        if re.search(pat, low):
            s += pts
            break
    if ':free' in low or 'free' in low:
        s -= 4
    if 'preview' in low or 'experimental' in low:
        s -= 2
    return s


def parse_reset(headers, default_seconds):
    for name in ('retry-after', 'x-ratelimit-reset-requests'):
        v = headers.get(name)
        if not v:
            continue
        try:
            x = float(v.strip())
            if x < 1e6:
                return max(5, int(x))
            now = time.time()
            if x > now:
                return max(5, int(x - now))
        except Exception:
            pass
    return default_seconds


def discover(p):
    try:
        req = Request(p['base'] + '/models', headers={'Authorization': 'Bearer ' + p['key'], 'User-Agent': 'PicoClaw-OmniRouter/1.0'})
        with urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode('utf-8', 'replace'))
        ids = [str(x['id']) for x in data.get('data', []) if isinstance(x, dict) and x.get('id')]
        with state_lock:
            ps = state.setdefault(p['id'], {'cooldown': 0.0, 'models': {}, 'ok': False, 'last_error': ''})
            for mid in ids:
                ps['models'].setdefault(mid, {'cooldown': 0.0, 'last_ok': 0.0, 'failures': 0, 'score': score_model(mid, p['priority'])})
            ps['models'] = {m: v for m, v in ps['models'].items() if m in ids}
            ps['ok'] = True
            ps['last_error'] = ''
        return ids
    except Exception as e:
        with state_lock:
            ps = state.setdefault(p['id'], {'cooldown': 0.0, 'models': {}, 'ok': False, 'last_error': ''})
            ps['ok'] = False
            ps['last_error'] = str(e)[:300]
        return []


def refresh():
    global last_refresh
    ps = provider_defs()
    for p in ps:
        discover(p)
    last_refresh = time.time()
    print('[Router] provider/model registry refreshed: ' + ', '.join(p['id'] for p in ps) if ps else '[Router] no hosted provider keys configured', flush=True)


def candidates():
    now = time.time()
    out = []
    for p in provider_defs():
        with state_lock:
            ps = state.get(p['id'], {})
            if ps.get('cooldown', 0) > now:
                continue
            models = dict(ps.get('models', {}))
        for mid, ms in models.items():
            if ms.get('cooldown', 0) > now or ms.get('score', -9999) < 0:
                continue
            penalty = min(ms.get('failures', 0) * 8, 40)
            out.append((ms.get('score', score_model(mid, p['priority'])) - penalty, p, mid))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def mark_failure(p, mid, status, headers):
    now = time.time()
    if status == 429:
        cooldown = parse_reset(headers, 86400)
        if cooldown < 30:
            cooldown = 60
    elif status in (401, 403):
        cooldown = 3600
    elif status in (400, 404):
        cooldown = 3600
    else:
        cooldown = 90
    with state_lock:
        ps = state.setdefault(p['id'], {'cooldown': 0.0, 'models': {}, 'ok': False, 'last_error': ''})
        ms = ps.setdefault('models', {}).setdefault(mid, {'cooldown': 0.0, 'last_ok': 0.0, 'failures': 0, 'score': score_model(mid, p['priority'])})
        ms['failures'] = ms.get('failures', 0) + 1
        ms['cooldown'] = now + cooldown
        ps['last_error'] = f'HTTP {status}; cooldown {cooldown}s'
        if status == 429:
            ps['cooldown'] = max(ps.get('cooldown', 0), now + cooldown)


def mark_success(p, mid):
    with state_lock:
        ps = state.setdefault(p['id'], {'cooldown': 0.0, 'models': {}, 'ok': True, 'last_error': ''})
        ms = ps.setdefault('models', {}).setdefault(mid, {'cooldown': 0.0, 'last_ok': 0.0, 'failures': 0, 'score': score_model(mid, p['priority'])})
        ms['last_ok'] = time.time()
        ms['failures'] = max(0, ms.get('failures', 0) - 1)
        ms['cooldown'] = 0.0
        ps['cooldown'] = 0.0
        ps['ok'] = True
        ps['last_error'] = ''


def upstream(p, mid, payload):
    body = dict(payload)
    body['model'] = mid
    raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
    req = Request(p['base'] + '/chat/completions', data=raw, headers={'Authorization': 'Bearer ' + p['key'], 'Content-Type': 'application/json', 'User-Agent': 'PicoClaw-OmniRouter/1.0'}, method='POST')
    return urlopen(req, timeout=TIMEOUT)


def local_upstream(payload):
    body = dict(payload)
    body['model'] = 'mobilellm-350m'
    raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
    req = Request(LOCAL_BASE + '/chat/completions', data=raw, headers={'Content-Type': 'application/json', 'Authorization': 'Bearer local'}, method='POST')
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
            e.read()
            mark_failure(p, mid, e.code, e.headers)
            errors.append(f"{p['id']}/{mid}: HTTP {e.code}")
        except Exception as e:
            mark_failure(p, mid, 503, {})
            errors.append(f"{p['id']}/{mid}: {type(e).__name__}")
    try:
        return local_upstream(payload), 'local', 'mobilellm-350m', errors
    except Exception as e:
        errors.append('local/mobilellm-350m: ' + type(e).__name__)
    raise RuntimeError('No working model route: ' + '; '.join(errors[-8:]))


class Handler(BaseHTTPRequestHandler):
    server_version = 'PicoClaw-OmniRouter/1.0'
    def log_message(self, fmt, *args):
        print('[Router] ' + fmt % args, flush=True)

    def _json(self, code, obj):
        raw = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == '/health':
            self._json(200, {'status': 'ok', 'hosted_candidates': len(candidates()), 'local_fallback': 'mobilellm-350m'})
            return
        if self.path.startswith('/v1/models'):
            if time.time() - last_refresh > REFRESH:
                refresh()
            data = [{'id': 'omniroute', 'object': 'model', 'owned_by': 'picoclaw-router'}]
            for _, p, mid in candidates():
                data.append({'id': f'{p["id"]}/{mid}', 'object': 'model', 'owned_by': p['id']})
            data.append({'id': 'mobilellm-350m', 'object': 'model', 'owned_by': 'facebook-mobilellm'})
            self._json(200, {'object': 'list', 'data': data})
            return
        self._json(404, {'error': 'not_found'})

    def do_POST(self):
        if self.path not in ('/v1/chat/completions', '/chat/completions'):
            self._json(404, {'error': 'not_found'})
            return
        try:
            n = int(self.headers.get('Content-Length', '0'))
            payload = json.loads(self.rfile.read(n).decode('utf-8'))
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


def monitor():
    global last_probe
    while True:
        try:
            refresh()
            if PROBE and time.time() - last_probe > PROBE_EVERY:
                cs = candidates()
                if cs:
                    _, p, mid = cs[0]
                    try:
                        rr = upstream(p, mid, {'model': mid, 'messages': [{'role': 'user', 'content': 'Reply with exactly: OK'}], 'max_tokens': 1, 'temperature': 0})
                        rr.close()
                        mark_success(p, mid)
                        print(f'[Router] probe OK -> {p["id"]}/{mid}', flush=True)
                    except HTTPError as e:
                        e.read(); mark_failure(p, mid, e.code, e.headers)
                        print(f'[Router] probe failed -> {p["id"]}/{mid}: HTTP {e.code}', flush=True)
                    except Exception as e:
                        mark_failure(p, mid, 503, {})
                        print(f'[Router] probe failed -> {p["id"]}/{mid}: {e}', flush=True)
                last_probe = time.time()
        except Exception as e:
            print('[Router] monitor error:', e, flush=True)
        time.sleep(REFRESH)


if __name__ == '__main__':
    refresh()
    threading.Thread(target=monitor, daemon=True).start()
    print(f'[Router] listening on {HOST}:{PORT}', flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
