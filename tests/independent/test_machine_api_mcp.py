"""V12: chạy probe MCP thật (client MCP <-> server MCP qua stdio, cổng giả) bằng uv.

Lượt 2: KHÔNG còn dung thứ các kiểm tra URL (trước đây `http://localhost@evil...` được chấp nhận). Thêm probe URL so khớp
urlparse (bộ kiểm của MCP) với httpx (nơi thật sự kết nối): thẻ không được tới máy khác máy đã duyệt.
"""
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UV = os.environ.get("UV_BIN") or shutil.which("uv") or "/Users/truong/.local/share/mise/installs/uv/latest/uv-aarch64-apple-darwin/uv"

# Sai khác ROBUSTNESS (không phải lộ thẻ): URL cấu hình dị dạng làm urlparse/httpx ném ValueError/InvalidURL thay vì ToolFailure.
ROBUSTNESS_ONLY = ("＃@", "https://[2001:db8::1", "https://api example.com")


@unittest.skipUnless(os.path.exists(UV), "cần uv để chạy MCP thật")
class McpProbe(unittest.TestCase):
    def run_probe(self, name):
        proc = subprocess.run([UV, "run", "--python", "3.12", str(ROOT / "tests/independent" / name)],
                              capture_output=True, text=True, cwd=str(ROOT), timeout=280)
        last = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")][-1]
        return json.loads(last), proc

    def test_mcp_probe_claim_v12_no_tolerated_failures(self):
        doc, proc = self.run_probe("mcp_probe_machine_api.py")
        self.assertEqual(doc["failed"], [], proc.stdout[-3000:])
        self.assertGreaterEqual(doc["total"], 90)

    def test_url_check_never_sends_token_to_a_different_host(self):
        doc, proc = self.run_probe("mcp_url_probe_machine_api.py")
        leaks = [v for v in doc["violations"] if "THẺ" in v[1] or "thẻ" in v[1] or "http tới máy không phải loopback" in v[1] or "userinfo" in v[1]]
        self.assertEqual(leaks, [], doc["violations"])
        # còn lại chỉ được là sai khác độ bền (URL dị dạng -> ngoại lệ thô), ghi nhận
        other = [v for v in doc["violations"] if v not in leaks]
        self.assertTrue(all(any(k in v[0] for k in ROBUSTNESS_ONLY) for v in other), other)
        self.assertGreaterEqual(doc["total"], 70)
        self.__class__.robustness_only = other


if __name__ == "__main__":
    unittest.main()
