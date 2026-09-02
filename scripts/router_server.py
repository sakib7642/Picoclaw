#!/usr/bin/env python3
import model_router
from http.server import ThreadingHTTPServer


def do_head(self):
    if self.path in ('/', '/health'):
        self.send_response(200)
        self.send_header('Content-Length', '0')
        self.end_headers()
        return
    if self.path.startswith('/v1/models'):
        self.send_response(200)
        self.send_header('Content-Length', '0')
        self.end_headers()
        return
    self.send_response(404)
    self.send_header('Content-Length', '0')
    self.end_headers()


model_router.Handler.do_HEAD = do_head

if __name__ == '__main__':
    model_router.load_state()
    model_router.refresh()
    model_router.threading.Thread(target=model_router.monitor, daemon=True).start()
    print(
        f'[Router] listening on {model_router.HOST}:{model_router.PORT}; '
        f'local model={model_router.LOCAL_MODEL_ID}',
        flush=True,
    )
    ThreadingHTTPServer((model_router.HOST, model_router.PORT), model_router.Handler).serve_forever()
