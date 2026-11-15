# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2", "httpx>=0.27,<1"]
# ///
"""Probe độc lập (lượt 2) cho bộ kiểm SEARCH_TOOLS_API_URL của MCP: so khớp urlparse (bộ kiểm) với httpx (nơi thật sự kết nối).

Chạy: uv run --python 3.12 tests/independent/mcp_url_probe_machine_api.py  -> in JSON ở dòng cuối; mã thoát 1 nếu có vi phạm.
Vi phạm = URL được bộ kiểm chấp nhận nhưng thẻ có thể bị gửi tới máy KHÁC máy mà bộ kiểm đã duyệt.
"""
import importlib.util
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import logging
import httpx
logging.disable(logging.CRITICAL)  # httpx INFO ghi URL (kèm q) ra stderr: không cần cho probe

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("search_tools_mcp", ROOT / "integrations/mcp/search_tools_mcp.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

TOKEN = "stm_urlprobe_SECRET_0123456789abcdef"
hits = {"good": [], "evil": []}


def make_handler(name):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits[name].append((self.path, self.headers.get("Authorization"), self.headers.get("Host")))
            data = b'{"results": []}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    return H


good = ThreadingHTTPServer(("127.0.0.1", 0), make_handler("good"))
evil = ThreadingHTTPServer(("127.0.0.1", 0), make_handler("evil"))
for srv in (good, evil):
    threading.Thread(target=srv.serve_forever, daemon=True).start()
P, E = good.server_address[1], evil.server_address[1]

candidates = [
    "http://localhost@evil.example", "http://localhost.evil.example", "https://user:pw@host.example", "http://user@localhost",
    "http://[::1]", f"http://[::1]:{P}", "http://[::1]:abc", "http://[0:0:0:0:0:0:0:1]", "http://[::ffff:127.0.0.1]", "http://[::1%25lo]",
    "http://127.0.0.1.evil.example", "http://127.0.0.1.", "http://localhost.", "http://0.0.0.0", "http://127.1", "http://2130706433",
    "http://LOCALHOST", "HTTP://localhost", " http://localhost ", "http://localhost/", "http://localhost//", "http://",
    "https://", "https:///x", "file:///etc/passwd", "ftp://localhost", "javascript:alert(1)", "http:localhost", "//localhost", "localhost",
    "http://localhost:80@evil.example", f"http://localhost:{P}\\@127.0.0.1:{E}", f"http://127.0.0.1:{E}\\@localhost:{P}",
    f"http://localhost:{P}#@127.0.0.1:{E}", f"http://localhost:{P}?@127.0.0.1:{E}", f"http://localhost:{P}/@127.0.0.1:{E}",
    f"http://127.0.0.1:{P}#.evil.example", f"http://127.0.0.1:{P}?x=http://127.0.0.1:{E}", f"http://localhost:{P}:{E}",
    f"http://localhost:{P}\t.evil", f"http://local\thost:{P}", f"http://localhost\n:{P}", f"http://localhost:{P}\r\nHost: evil",
    f"http://127.0.0.1:{E}\\.localhost:{P}", f"http://127.0.0.1:{P}%2F@127.0.0.1:{E}", f"http://127.0.0.1:{P}%40127.0.0.1:{E}",
    f"http://127.0.0.1:{P}%00.evil", f"http://127.0.0.1%00.evil:{P}",
    "http://localhost．evil.example", "http://localhost。evil.example", "http://ｌｏｃａｌｈｏｓｔ",
    f"http://localhost:{P}∕@127.0.0.1:{E}", f"http://127.0.0.1:{P}＃@127.0.0.1:{E}", f"http://127.0.0.1:{P}\\\\@127.0.0.1:{E}",
    f"http://127.0.0.1:{P}/machine/v1", f"http://localhost:{P}/machine/v1", f"http://LocalHost:{P}/machine/v1/",
    f"http://[::1]:{P}/machine/v1", "https://api.example.com/machine/v1", "https://api.example.com:8443/machine/v1?x=1",
    "https://api.example.com\\@evil.example/machine/v1", "https://api.example.com#@evil.example", "https://api.example.com?@evil.example",
    "https://api.example.com\\.evil.example/machine/v1", "https://evil.example\\@api.example.com", "https://api.example.com%2f@evil.example",
    "https://ａｐｉ.example.com/machine/v1", "https://api.example.com:443:444", "https://api.example.com:99999",
    "https://api.example.com:-1", "http://localhost:99999", "http://localhost:-1", "http://localhost:0",
    "HtTpS://api.example.com", "https://api example.com", "https://api.example.com ", "https://[2001:db8::1]/machine/v1",
    "https://[2001:db8::1", "https://2001:db8::1/machine/v1", "http://localhost:{P}",
]
violations, report = [], []
LOCAL = {"localhost", "127.0.0.1", "::1"}
for url in candidates:
    os.environ["SEARCH_TOOLS_API_URL"] = url
    os.environ["SEARCH_TOOLS_API_TOKEN"] = TOKEN
    os.environ["SEARCH_TOOLS_API_TIMEOUT"] = "2"
    try:
        base, _tok = mod._config()
    except mod.ToolFailure:
        report.append((url, "rejected"))
        continue
    except Exception as exc:  # noqa: BLE001
        report.append((url, "config-crash:" + type(exc).__name__))
        violations.append((url, "ToolFailure mong đợi, nhận " + type(exc).__name__))
        continue
    parsed = urlparse(base)
    for srv in hits.values():
        srv.clear()
    outcome = "accepted"
    try:
        target = httpx.URL(base + "/products")
        actual_host = target.host
        actual_user = target.userinfo
    except Exception as exc:  # noqa: BLE001
        actual_host = None
        actual_user = b""
        outcome = "accepted-but-httpx-invalid:" + type(exc).__name__
    if actual_host is not None:
        expected = (parsed.hostname or "").lower()
        if parsed.scheme == "http" and expected not in LOCAL:
            violations.append((url, "http tới máy không phải loopback"))
        norm_actual = actual_host.lower().strip("[]")
        if norm_actual != expected.strip("[]"):
            violations.append((url, f"httpx kết nối {actual_host!r} khác bộ kiểm {expected!r}"))
        if actual_user:
            violations.append((url, "httpx thấy userinfo"))
    # gọi thật: thẻ có tới máy lạ không?
    try:
        mod._call("/products", {"q": "abcdef"})
        outcome += ";call-ok"
    except mod.ToolFailure as exc:
        outcome += ";call-failure"
    except Exception as exc:  # noqa: BLE001
        outcome += ";call-crash:" + type(exc).__name__
        if TOKEN in repr(exc) or TOKEN in str(exc):
            violations.append((url, "thẻ nằm trong thông điệp lỗi"))
    if hits["evil"]:
        violations.append((url, "THẺ ĐẾN MÁY LẠ " + repr(hits["evil"])))
    report.append((url, outcome, "good=%d" % len(hits["good"])))
print(json.dumps({"total": len(candidates), "violations": violations, "report": report}, ensure_ascii=False))
sys.exit(1 if violations else 0)
