import importlib.util
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


_MODULE_PATH = Path(__file__).resolve().parents[1] / 'code' / 'scripts' / 'wait_for_dashboard_ready.py'
_SPEC = importlib.util.spec_from_file_location('dashboard_wait_ready_test_module', _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
wait_mod = importlib.util.module_from_spec(_SPEC)
sys.modules.setdefault('dashboard_wait_ready_test_module', wait_mod)
_SPEC.loader.exec_module(wait_mod)


class _OkHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{}')

    def log_message(self, format, *args):
        return


def test_wait_for_dashboard_ready_succeeds_on_healthy_endpoint():
    server = ThreadingHTTPServer(('127.0.0.1', 0), _OkHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        ok, message = wait_mod.wait_for_dashboard_ready(
            url=f'http://127.0.0.1:{server.server_port}/api/data',
            timeout_seconds=1.5,
            interval_seconds=0.05,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1.0)

    assert ok is True
    assert 'dashboard ready' in message