# Verification Result — machine-api-batch (lượt 3, hợp đồng v2)

Lượt trước: [lượt 1](VERIFICATION_RESULT_ROUND1.md) (FAIL B2, B4, B7), [lượt 2](VERIFICATION_RESULT_ROUND2.md) (FAIL B4 + SPEC_AMBIGUOUS → PO chốt hợp đồng v2 ngày 2026-11-27). Verifier lượt 3 ghi kết quả bên dưới.

## Task status

attempt: 2 / max_auto_repairs: 2  (lượt 1 FAIL B2, B4, B7 → sửa lần 1; lượt 2 FAIL B4 → escalate, PO 2026-11-27 duyệt hợp đồng v2 + sửa lần 2)
same_claim_repeat_fail: B4 (lượt 1 và lượt 2 liên tiếp; PO đã quyết)
next_action: HUMAN_REVIEW

Verifier lượt 3 (2026-11-27, ngữ cảnh sạch, hợp đồng v2). Mã kiểm: worktree `feature/machine-api-batch` (chưa commit), `machine_api.py` sha256 `4b44868f6546…` — trùng bản đang chạy trên staging (`/srv/search-tools-machine-api/app/machine_api.py`). Test độc lập mới: `tests/independent/test_machine_api_batch_round3.py` (6 test). Test lượt 1, lượt 2 chạy lại nguyên trạng, không sửa.

Lệnh đã chạy (DATABASE_URL local, PYTHONPATH=.:tests:tests/independent):
- `unittest tests.test_machine_api tests.test_machine_api_internal tests.test_machine_api_batch` → **Ran 108, OK**.
- `unittest discover -s tests/independent -p "test_machine_api*.py"` (chạy trước khi tạo file lượt 3) → **Ran 283: 280 OK, 3 FAIL** — cả 3 là test lượt 2 kiểm `same_cas` theo cách hiểu đã bị hợp đồng v2 thay thế (xem B4).
- `unittest tests.independent.test_machine_api_batch_round3` → **Ran 6, OK**.

Kết luận nhanh: B1 PASS · B2 PASS · B3 PASS · B4 PASS · B5 PASS · B6 PASS · B7 PASS · B8 PASS · B9 NOT RUN (UAT sau).

## Claim B1 — Không hồi quy

Test thực hiện: 3 bộ test của agent chính; toàn bộ `tests/independent/test_machine_api*.py`; `B1_OldVsNew` (lượt 1) chạy `machine_api.py` của `origin/main` trong tiến trình riêng và so từng phản hồi GET với bản mới; `git diff origin/main --stat`.
Kết quả: PASS
Evidence: 108/108 OK. `B1_OldVsNew`: 399 GET (`/products`, `/compliance`, `/stock`, 7 thẻ) — status {200: 332, 400: 49, 403: 18}, **0 khác biệt**. Mọi test độc lập ngoài 3 test lượt 2 nói ở B4 đều PASS. Diff chỉ chạm `machine_api.py`, `deploy/nginx-machine-api.conf.example`, tài liệu, test; hai test độc lập cũ chỉ đổi đúng như Change request log; không chạm `search.py` hay middleware ứng dụng nhân viên. `_stock_lots` (dùng bởi `/products`) giữ nguyên thứ tự và trần lô như `origin/main`.
Negative case đã thử: so cũ/mới trên dữ liệu có ca biên (CAS 5005 lô, mã có tab/khoảng trắng, `% _ !`, thẻ quyền hẹp) — 0 khác.
Residual risk: `MAX_CONTENT_LENGTH` của tiến trình máy nâng 64 KB → 128 KB cho mọi đường (đường GET vẫn bị Nginx chặn ở 8 KB, xem B8); so cũ/mới chỉ trên dữ liệu thử.

## Claim B2 — Đủ mục, đúng thứ tự, trạng thái tường minh

Test thực hiện: test lượt 1+2 (mục trùng/khác hoa thường/khoảng trắng, 19 kiểu mục sai, 500 mục trùng, 500 mục sai, surrogate, ký tự C1, thân hỏng); staging: 500 mã thật có `ref` `r0..r499`; lượt 3: gói 23 mục trộn và gói đảo ngược.
Kết quả: PASS
Evidence: toàn bộ test B2 lượt 1+2 PASS. Staging: 500 mục → `count=500`, `position`=k và `ref` khớp với mọi k, summary `found=500`. 500 mục `VBPROBE…` có `ref` 64 ký tự (thân 50 KB) → 200, `not_found=500`. Lượt 3: gói 23 mục (có mục `{"code": 5}`) và cùng gói đảo thứ tự → từng mục giống hệt nhau trừ `position`.
Negative case đã thử: như trên; mục số nguyên giữa gói chỉ làm mục đó `INVALID`.
Residual risk: như lượt 2 — ký tự điều khiển ở đầu/cuối mà Python coi là khoảng trắng bị bỏ như khoảng trắng.

## Claim B3 — Khớp CHÍNH XÁC, trả MỌI sản phẩm khớp, không tự chọn

Test thực hiện: test lượt 1 (đối chiếu SQL độc lập, mã "chứa"/tiền tố/tab, `% _ !`, xen đều hãng khi cắt, ổn định, trùng mã khác hãng/quy cách); staging: 500 mã ngẫu nhiên, 500 CAS phổ biến; đối chiếu `matches_total`/NOT_FOUND với các sản phẩm cùng mã chính xác trong trang `/products`.
Kết quả: PASS
Evidence: test lượt 1 PASS. Staging, 40 mã mỗi thẻ: không mục NOT_FOUND nào mà `/products` có sản phẩm cùng mã chính xác; `matches_total` bằng số sản phẩm cùng mã trên trang `/products` (trang không đầy) — 0 lệch, cả thẻ nội bộ và thẻ khách. 500 mã → 559 sản phẩm (mã nhiều sản phẩm trả đủ). 500 CAS phổ biến, `max_per_item` 20 → 500 `truncated`.
Negative case đã thử: mã tiền tố/chuỗi con, ký tự đại diện (test lượt 1); staging DB collation `en_US.UTF-8`, `upper('äbc-ß')` = `ÄBC-ß` nên chữ có dấu cũng không phân biệt hoa thường (hai vế cùng UPPER, giống `/products`).
Residual risk: trên staging không có mã nào trùng nhiều hãng vượt `max_per_item` để thử xen đều hãng với dữ liệu thật (chỉ thử trên dữ liệu thử).

## Claim B4 — Trùng khớp với `/products` (giá, pháp chế, tồn kho) — hợp đồng v2

Test thực hiện (lượt 3, `test_machine_api_batch_round3.py`), dữ liệu biên mới: CAS 551-00-1 với mã hỏi `AAB-OWN` có 3.000 lô xếp TRƯỚC mọi mã khác (cửa sổ phải nới đúng phần lô của chính mã) + 3 lô CAS khác; `AAC-OTHER` 5.000 lô mà **chỉ lô thứ 5.000** còn hàng (lệch 1 lô là mất gợi ý); `PLAIN-551` hai sản phẩm (BETA, ZETA); 12 mã cùng CAS 552-00-2 (cắt 10/5); `TWIN-9` một mã hai CAS, CAS thứ nhất 5001 lô mã khác; lặp lại ca FAIL lượt 2 (`BIGLOT-A` 50.000 lô + `ZLOT-B`) và `DUAL-1`. Gói 23 mục trộn mã/CAS (kèm dữ liệu lượt 1/2: VX-100, MULTI-1, CCC-ASK, 777-77-7, ZZZ-BIG, 555-55-5…), 4 thẻ (internal, cust, wide, wideq), include đủ và chỉ `stock`:
1. Không phụ thuộc mục khác: mỗi mục trong gói 23 mục so với chính nó gửi riêng (gói 1 mục).
2. `same_cas`/`same_cas_more` so với oracle viết lại bằng SQL độc lập theo đúng định nghĩa v2 (5.000 lô đầu tiên có CAS X, mã ≠ C, hãng thẻ thấy, thứ tự `brand_norm, code_norm, size_norm, expiry NULLS LAST`; nhóm/còn hàng/cắt 5|10).
3. Toàn bộ JSON so với `/products?q=<mã>&limit=1` khi trang đó có đúng sản phẩm này.
4. Giá, pháp chế, `stock` (trừ `same_cas*`) so với `/products?q=<mã>` trang mặc định (limit 50 nội bộ / 10 khách).
Staging (dữ liệu thật): 31 mã có tồn kho + 35 mã ngẫu nhiên (thẻ nội bộ) và 35 mã ngẫu nhiên tìm được + 5 không tìm được (thẻ khách team 166), so toàn bộ JSON với `/products?q=<mã>`.
Kết quả: PASS
Evidence:
- (1) 0 khác biệt (4 thẻ × 2 kiểu include × 23 mục). Ca FAIL lượt 2: `ZLOT-B` gửi riêng / cùng `BIGLOT-A` / `/products` = (`IN_STOCK`, 3) / (`IN_STOCK`, 3) / (`IN_STOCK`, 3) — test lượt 2 `test_code_lot_cap_is_not_shared_across_batch` nay PASS.
- (2) 155 sản phẩm có CAS được so, 0 khác. Biên: `AAB-OWN` → `same_cas=[AAC-OTHER]` (đúng lô thứ 5.000), `lots_total=3003`; `PLAIN-551` BETA → `[AAB-OWN]`.
- (3) 87 sản phẩm, 0 khác.
- (4) 166 sản phẩm, 0 khác (đủ dạng giá OK/thiếu tỷ giá/giá hỏng, pháp chế có/không quy tắc, có/không tồn kho).
- Staging: thẻ nội bộ 31 + 38 sản phẩm, thẻ khách 43 sản phẩm — **0 khác**; phía nội bộ có 45 sản phẩm `price_status=OK`, 31 có `lots`, 11 có quy tắc pháp chế; thẻ khách không có khóa giá.
- 3 test lượt 2 vẫn FAIL: `test_contains_match_code_in_products_page` (CCC-ASK; trang `/products` limit 50 có thêm mã "chứa" CCC-ASK-X), `test_same_code_two_cas_values` (DUAL-1), `test_cas_item_and_mixed_batch_parity` (chỉ lệch CCC-ASK). Cả 3 đối chiếu `same_cas` với trang `/products` mặc định đúng trong các ca hợp đồng v2 nói phải "đối chiếu theo định nghĩa thay vì theo trang `/products`". Theo định nghĩa (oracle lượt 3, gói có cả CCC-ASK và DUAL-1) batch đúng. Không tính là FAIL: **các test này kiểm theo cách hiểu đã bị hợp đồng v2 thay thế**.
Negative case đã thử: lô của chính mã chen trước cửa sổ; lô còn hàng duy nhất nằm đúng vị trí 5.000; một mã hai CAS; hai mục cùng CAS có số lô riêng khác nhau; mục trùng mã khác hoa thường/khoảng trắng; đảo thứ tự gói; thẻ khách không thấy lô của hãng ngoài team.
Residual risk: staging chỉ có 763 lô đang hoạt động (chỉ ≈ 1 sản phẩm có `same_cas` thật), nên các ca trần 5.000/50.000 chỉ chứng minh trên dữ liệu thử. Mã có > 50.000 lô (chính `/products` cũng chạm trần, hợp đồng miễn) chưa thử. Truy vấn lô theo CAS đọc mọi lô của từng CAS rồi mới cắt — chưa đo với khối lượng tồn kho lớn (không truy cập production).

## Claim B5 — Quyền và bảo mật giữ nguyên

Test thực hiện: test lượt 1+2 (6 thẻ khách × include, header/khóa nâng quyền, quyền trường, NOT_ALLOWED, 401/403/503/405/413, một lời gọi = một lượt tần suất, giao dịch chỉ-đọc, gợi ý cùng CAS không lộ hãng ngoài team); staging: thẻ khách 500 mã + 500 CAS, đủ include; GET lên đường batch; gọi không thẻ.
Kết quả: PASS
Evidence: test PASS. Staging thẻ khách: không có khóa `price/note/stock_price_vnd/stock_note/unit_price_vnd/original_price` trong phản hồi; 500 mã → 212 FOUND (giới hạn hãng team 166); 31 mã tồn kho của hãng ngoài team → NOT_FOUND và `/products` thẻ khách cũng không có mã đó. GET → 405 `http_error`; không thẻ → 401. 3 gói 500 mục liền nhau → 200 cả ba (mỗi gói một request).
Negative case đã thử: như trên.
Residual risk: như lượt 2 (team thiếu SEARCH bị từ chối cả lời gọi, giống `/products`).

## Claim B6 — Nhật ký không rò bí mật

Test thực hiện: test lượt 1 (bắt log, thân lỗi không lặp giá trị); staging: tìm các giá trị đã gửi (`VB-NGINX-PROBE`, `VBPROBE…`, `VB-RATE`, 20 mã thật, CAS) trong journal `search-tools-machine-api` và `machine_api.access.log` từ 13:35 UTC.
Kết quả: PASS
Evidence: 297 dòng journal đều là dòng truy cập JSON `{ts, token(id), endpoint, status, results, ip, ms}`; 0 lần xuất hiện các giá trị đã gửi (một "trúng" duy nhất là CAS `-` khớp dấu gạch trong dấu thời gian, không phải rò rỉ). 306 dòng Nginx dạng `IP [time] "POST /machine/v1/products/batch" status bytes time` — không query, không thân; 0 dòng có `?`/`q=`.
Negative case đã thử: thân 129 KB, sai đường, GET lên batch — không sinh traceback.
Residual risk: nhánh `audit_log.exception` (lỗi DB) không kích hoạt được bằng đầu vào batch.

## Claim B7 — Nhanh, dùng chỉ mục, không ảnh hưởng ứng dụng nhân viên

Test thực hiện (staging, qua Nginx+TLS, 1.262.173 sản phẩm): 500 mã ngẫu nhiên có thật, `include` đủ, thẻ nội bộ (2 lần); 500 CAS phổ biến nhất (thẻ nội bộ max_per_item 20/1/50, thẻ khách); 500 mã có nhiều sản phẩm nhất (max_per_item 50; thẻ khách); `/login` gọi mỗi 0,5 giây trong lúc chạy; `EXPLAIN (ANALYZE, BUFFERS)` đúng các câu SQL batch (sinh từ mã, 500 giá trị) trên staging trong giao dịch chỉ-đọc.
Kết quả: PASS
Evidence: 500 mã → 200, **0,38 s** (lần 2: 0,28 s), 570 KB. 500 CAS nội bộ (10.000 sản phẩm, đúng trần) → 200, **3,63 s**; max_per_item 1 → 0,89 s; max_per_item 50 → 400 `too_many_results` (1,48 s); thẻ khách → 200, 1,39 s. 500 mã nhiều sản phẩm nhất → 200, 0,47 s (993 KB); thẻ khách 0,38 s. Không có 5xx. Kế hoạch: `Index Scan using idx_products_code_upper_trim` (loops=500), `Bitmap Index Scan on idx_products_cas_upper_trim` (loops=500); **không có Seq Scan trên `products`**; SQL theo mã 49 ms, theo CAS 668 ms (nội bộ) / 479 ms (khách); SQL lô < 5 ms. `/login`: 200 trước, 16/16 lần 200 trong lúc chạy (chậm nhất 0,10 s), 200 sau.
Negative case đã thử: 500 CAS phổ biến nhất với trần cao nhất; thẻ khách (lọc hãng); mã nhiều sản phẩm nhất.
Residual risk: `stock_items` trên staging bị Seq Scan vì bảng nhỏ (2.295 dòng, 763 lô hoạt động) — ngoài oracle (chỉ `products`), nhưng thời gian với tồn kho lớn chưa đo.

## Claim B8 — Nginx chỉ nới đúng đường batch

Test thực hiện (staging, qua Nginx+TLS): đọc `/etc/nginx/snippets/machine-api-location.conf`; `nginx -t`; thân đệm khoảng trắng 100/127/129 KB tới batch; 100 KB tới `/machine/v1/products` (POST và GET) và `/stock`; 9 KB tới `/products`; biến thể đường (`batch/`, `batchx`, `batch?x=1`, `//products/batch`, `%62atch`); `/login` trước/sau.
Kết quả: PASS
Evidence: cấu hình: `location /machine/` giữ `client_max_body_size 8k`; chỉ `location = /machine/v1/products/batch` có `128k`. `nginx -t` đạt. Batch 100 KB → 200 (ứng dụng trả NOT_FOUND hợp lệ), 127 KB → 200, 129 KB → **413 từ nginx**. `/products` 100 KB POST/GET → 413, `/products` 9 KB → 413, `/stock` 100 KB → 413, `batch/` → 413, `batchx` → 413. `batch?x=1` và `%62atch` → 200 (Nginx chuẩn hóa thành đúng đường batch). `//products/batch` → Nginx gộp dấu `/` nên nhận thân, ứng dụng trả 308 về đường batch — không mở đường nào khác. `/login` 200 trước và sau. Log không chứa thân (B6).
Negative case đã thử: như trên.
Residual risk: lượt này không reload Nginx (cấm sửa/khởi động lại); "200 trước và sau reload" dựa trên cấu hình đang chạy.

## Claim B9 — Chạy thật qua n8n (UAT staging)

Kết quả: NOT RUN (UAT sau, cùng chủ dự án).

Dọn dẹp staging: file tạm `/tmp/vb_*`, `/tmp/vbx_*`, `/tmp/vbw/` (header thẻ 0600) đã xóa; không in giá trị thẻ; database chỉ đọc (`default_transaction_read_only=on`, `BEGIN READ ONLY`); không restart, không sửa file cấu hình.

## Cross-vendor review lượt 2 (Codex chỉ-đọc, 2026-11-27, do agent chính ghi)

Phạm vi: bản xuất sạch mới (bản `4b44868f…`), `BATCH_CHANGE.diff`, hợp đồng v2. Kết luận Codex: chưa thấy rò giá/ghi chú cho thẻ khách, lọt hãng ngoài team (kể cả `same_cas`) hay SQL injection. Phát hiện:

1. "Chặn" (B4 v2): cửa sổ `same_cas` tính `5.000 + số lô của chính mã` từ danh sách lô theo mã đã cắt ở 50.000; nếu MỘT mã có > 50.000 lô (ở CAS khác) thì cửa sổ có thể nhỏ hơn định nghĩa và phụ thuộc mục khác. Codex xác nhận bằng mô phỏng, chưa chạy PostgreSQL. Agent chính: ngân sách sửa đã hết (2/2) → trình PO; dữ liệu hiện tại không thể chạm (toàn bộ tồn kho staging 763 lô; một mã cần > 50.000 lô).
2. Nên sửa: 3 test độc lập lượt 2 (`test_contains_match_code_in_products_page`, `test_same_code_two_cas_values`, `test_cas_item_and_mixed_batch_parity`) còn kiểm theo cách hiểu cũ (trùng kết luận verifier lượt 3). Verifier lượt 3 đề nghị chuyển sang định nghĩa v2 nhưng hệ thống quyền chặn sửa test kiểm chứng → chờ PO.
3. Ghi nhận: ký tự điều khiển ở ĐẦU/CUỐI mà Python coi là khoảng trắng (`\x1c`–`\x1f`, `\x85`) bị bỏ như khoảng trắng nên mục vẫn có thể FOUND (ký tự ở giữa → INVALID). Không có đường rò dữ liệu.
