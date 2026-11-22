"""Trợ giúp của VERIFIER độc lập: chạy một loạt lời gọi vào BẢN CŨ của cổng (commit 351cf1a) trong tiến trình riêng.

Dùng cho test hồi quy so sánh thẻ khách trước/sau (claim I1/I8). Không phải mã ứng dụng.
Cách dùng: runner.py <thư mục chứa machine_api.py cũ> <tệp JSON yêu cầu>  (biến môi trường: DATABASE_URL, MACHINE_API_TOKENS_FILE)
Đầu ra: JSON list [{status, body}] theo đúng thứ tự yêu cầu.
"""
import json
import os
import sys

old_dir, requests_path = sys.argv[1], sys.argv[2]
sys.path.insert(0, old_dir)
os.environ.setdefault("FLASK_SECRET_KEY", "machine-api-verifier-only")
import machine_api  # noqa: E402

assert machine_api.__file__.startswith(old_dir), machine_api.__file__
machine_api.CONFIG_RECHECK_SECONDS = 0.0
client = machine_api.app.test_client()
out = []
for req in json.load(open(requests_path, encoding="utf-8")):
    resp = client.get(req["path"], headers={"Authorization": "Bearer " + req["token"]}, query_string=req.get("query") or None,
                      environ_overrides={"REMOTE_ADDR": "198.51.100.7"})
    try:
        body = resp.get_json()
    except Exception:  # noqa: BLE001
        body = resp.get_data(as_text=True)
    out.append({"status": resp.status_code, "body": body})
    machine_api.rate_limiter.reset()
print(json.dumps(out, ensure_ascii=False))
