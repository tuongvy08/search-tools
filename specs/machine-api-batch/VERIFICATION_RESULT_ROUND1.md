# Verification Result — machine-api-batch (hợp đồng v1)

Do verifier độc lập tạo, không do agent chính tự viết. Verifier không thấy lịch sử hội thoại, kế hoạch sửa lỗi hay giải thích của agent chính — chỉ nhận yêu cầu gốc của PO, VERIFICATION.md đã khóa, diff code (worktree `feature/machine-api-batch`, chưa commit, so `origin/main` = e1d6f9c), lệnh test và quyền đọc staging.

Ngày: 2026-11-26. Code trên staging trùng code worktree (sha256 `machine_api.py` = `8d1b7136…1625` ở cả hai nơi).

Test độc lập của verifier: `tests/independent/test_machine_api_batch_independent.py` (22 test; 19 PASS, 3 FAIL — chính là bằng chứng của B2 và B4 bên dưới).
Chạy: `PYTHONPATH=.:tests:tests/independent DATABASE_URL=… .venv/bin/python -m unittest tests.independent.test_machine_api_batch_independent`

Tóm tắt: B1 PASS · **B2 FAIL** · B3 PASS · **B4 FAIL** · B5 PASS (có ghi chú) · B6 PASS · **B7 FAIL** · B8 PASS · B9 NOT RUN (UAT sau).

## Claim B1 — Không hồi quy

Test thực hiện: (1) chạy toàn bộ test có sẵn; (2) so số test với `origin/main`; (3) test độc lập `B1_OldVsNew`: chạy BẢN `origin/main:machine_api.py` trong tiến trình riêng (runner có sẵn `tests/independent/machine_api_internal_old_runner.py`) và bản mới trên CÙNG database thử, so JSON từng phản hồi; (4) xem danh sách file trong diff.
Kết quả: PASS
Evidence:
- `tests.test_machine_api tests.test_machine_api_internal tests.test_machine_api_batch`: Ran 103, OK (52 + 33 + 18 test batch mới).
- `discover -s tests/independent -p "test_machine_api*.py"` (chưa gồm file của verifier): Ran 250, OK, 255,8 giây.
- Đếm `def test_` từng file so `origin/main`: không file nào giảm (52/52, 33/33, 87/87, 56/56, 31/31, 18/18, 9/9, 2/2, 25/25, 22/22). Hai test độc lập cũ chỉ đổi phép so danh sách đường openapi đúng như "Change request log".
- Old-vs-new: 399 lời gọi GET `/products` (có/không `limit=50`), `/compliance`, `/stock` × 7 thẻ (nội bộ, khách đầy đủ, khách hạn chế, không VIEW_CODE, không VIEW_CAS, khách rộng, khách có số lượng) → trạng thái {200: 332, 400: 49, 403: 18}, **0 khác biệt** JSON/mã trạng thái.
- `git diff origin/main --name-only`: không có `search.py` hay module middleware nào của ứng dụng nhân viên.
Negative case đã thử: dữ liệu có mã lưu kèm khoảng trắng/tab, mã chứa `% _ !`, CAS có > 5000 lô (đúng vùng mà refactor `_index_lots`/`_result_item` có thể làm lệch) — `/products`, `/stock` vẫn y hệt bản cũ.
Residual risk: `MAX_CONTENT_LENGTH` của ứng dụng máy tăng 64 KB → 128 KB cho MỌI đường (các đường GET vẫn được Nginx chặn 8 KB trên staging; chạy thẳng cổng 8100 thì không).

## Claim B2 — Đủ mục, đúng thứ tự, trạng thái tường minh

Test thực hiện: `B2_OrderStatus` — 28 mục trộn (trùng, khác hoa thường, khoảng trắng/tab đầu cuối, số, null, danh sách, bool, chuỗi trần, thiếu/cả hai trường, khóa lạ, rỗng sau khi bỏ khoảng trắng, 100/101 ký tự, `\x01`, `\x7f`, ref 64/65 ký tự, ref null); 500 mục trùng một mã; 500 mục sai ở 5 dạng; 500 mã khác nhau (102 có, 398 không); mục có chuỗi chứa ký tự UTF-16 lẻ (lone surrogate, JSON hợp lệ `"\ud800"`), cả trong `code` và trong `ref`; ký tự điều khiển C1; JSON lồng sâu.
Kết quả: **FAIL**
Evidence:
- Đạt: đúng N phần tử, `position` = k, `ref` trả lại nguyên văn (kể cả `""` và 64 ký tự), mọi dạng sai liệt kê trong hợp đồng → `INVALID` + `message`, `results=[]`, `matches_total=0`, `truncated=false`; `summary` khớp đếm độc lập; 500 trùng/500 sai/500 khác nhau đều đủ dòng và đúng thứ tự. Staging: 500 mã thật và 500 CAS → `count=500`, `order_ok=True` (khớp từng `ref`).
- **Không đạt**: một mục có chuỗi chứa ký tự UTF-16 lẻ làm hỏng CẢ lời gọi:
  - `{"items":[{"code":"VX-100"},{"code":"AB\ud800CD"},{"code":"MULTI-1"}]}` → HTTP 500 `internal_error`; nhật ký: `UnicodeEncodeError: 'utf-8' codec can't encode character '\ud800' … surrogates not allowed` tại `_batch_rows` (cur.execute). Hai mục hợp lệ còn lại không được trả.
  - `{"items":[{"code":"VX-100","ref":"x\udc00"},{"code":"MULTI-1"}]}` → HTTP 500; lỗi tại `_respond` → `jsonify` (không mã hóa được phản hồi).
  Hợp đồng: "Mục sai (… không phải chuỗi, … ký tự điều khiển, khóa lạ) → INVALID kèm message, **không** làm hỏng các mục khác". Chuỗi kiểu này có thể sinh ra khi n8n/JavaScript cắt chuỗi giữa một emoji.
Negative case đã thử: như trên; thêm hai quan sát không dùng để kết luận FAIL:
- (SPEC_AMBIGUOUS) ký tự điều khiển C1 (U+0080..U+009F, ví dụ `"VX\u009b100"`) → `NOT_FOUND`, không phải `INVALID`; hợp đồng không nói rõ "ký tự điều khiển" có gồm C1 hay không. Không làm hỏng mục khác.
- JSON lồng sâu (`[`×50000…) → HTTP 500 `internal_error` (RecursionError khi đọc JSON) thay vì 400; thân phản hồi chung chung, không rò nội dung. Hợp đồng không ghim mã lỗi cho trường hợp này.
Residual risk: chưa thử thân JSON có khóa trùng nhau hoặc mã hóa không phải UTF-8.

Classification: IMPLEMENTATION_FAIL
Reproduction: `tests.independent.test_machine_api_batch_independent.B2_OrderStatus.test_lone_surrogate_item_does_not_break_other_items` và `…test_lone_surrogate_in_ref_does_not_break_call`; hoặc gửi bằng thẻ nội bộ thân thô `{"items": [{"code": "VX-100"}, {"code": "AB\ud800CD"}]}` (Content-Type application/json) → 500.
Suggested area: kiểm tra giá trị `code`/`cas`/`ref` của từng mục trong `_parse_batch_item`.

## Claim B3 — Khớp CHÍNH XÁC, trả MỌI sản phẩm khớp, không tự chọn

Test thực hiện: `B3_ExactMatch` — so `matches_total` với truy vấn SQL độc lập `UPPER(TRIM(field)) = UPPER(giá trị đã strip)`; mã lưu trong DB `"VX-100"`, `" vx-100"`, `"Vx-100  "` (3 hãng/quy cách) cộng các bẫy `AVX-100` (chứa), `VX-1000` (tiền tố), `"VX-100\t"`; ký tự `% _ !` và giá trị chỉ là `%`, `_`, `U%`; 28 sản phẩm cùng mã ở 7 hãng × 4 quy cách với `max_per_item` 3/7/10 (thẻ nội bộ) và 5/10 (thẻ khách, 6 hãng thấy được); gọi lại 3 lần với giá trị khác hoa thường + khoảng trắng.
Kết quả: PASS
Evidence: `vx-100` → đúng 4 sản phẩm (khớp SQL độc lập), không có `AVX-100`/`VX-1000`/`VX-100\t`; `VX-10`, `22-22-2` → NOT_FOUND; `U_1`, `P%1`, `B!1` chỉ trả đúng mã đó, `%`/`_`/`U%` → NOT_FOUND; `MULTI-1`: `matches_total` 28 (nội bộ)/24 (khách), `truncated` đúng, số hãng khác nhau ≥ min(số hãng, max_per_item) ở mọi mức cắt; kết quả giống hệt giữa các lần gọi. CAS khớp cả sản phẩm có mã NULL/rỗng (`333-33-3` → 2). Staging: 500 CAS phổ biến (thẻ nội bộ) → `truncated` 500/500, 10.000 kết quả, đúng 20/mục.
Negative case đã thử: như trên (chứa, tiền tố, tab, ký tự đại diện, cắt ở nhiều mức).
Residual risk: chuẩn hóa khoảng trắng phía người gọi dùng `str.strip()` của Python (bỏ cả khoảng trắng Unicode như NBSP) trong khi phía DB dùng `TRIM` (chỉ dấu cách) — cùng cách với `/products`; chưa thấy trường hợp thật.

## Claim B4 — Trùng khớp với `/products` (giá, pháp chế, tồn kho)

Test thực hiện: `B4_Parity` — mỗi sản phẩm batch so với phần tử cùng (name, code, brand, size, cas) của `/products?q=<mã>` cho 5 thẻ (nội bộ, khách đầy đủ, khách hạn chế, khách rộng, khách có số lượng), 54 mã (giá bẩn nan/inf/1e308, nhiều tiền tố tỷ giá, hãng thiếu tỷ giá/ngừng, lô 25/205, > 5000 lô cùng CAS); thêm ca biên: sản phẩm `CCC-ASK` (CAS 777-77-7) có 5005 lô của MỘT mã khác (`AAA-ONE`) và 1 lô của mã thứ hai (`BBB-TWO`, xếp sau). Staging: so 32 sản phẩm (thẻ nội bộ) + 16 (thẻ khách) từ batch 500 mã thật với `/products` qua HTTPS.
Kết quả: **FAIL**
Evidence:
- Đạt: ma trận 5 thẻ × 54 mã: 0 khác biệt (≥ 60 sản phẩm so). Staging: 48 sản phẩm, 0 khác biệt (giá OK/NO_PRICE_DATA, USD/EUR/VND; pháp chế RULE/NO_RULE; tồn kho có/không `same_cas`).
- **Không đạt**: ca biên `CCC-ASK`, thẻ nội bộ: batch `stock.same_cas` = `['AAA-ONE', 'BBB-TWO']`, `/products` = `['AAA-ONE']` → JSON khác nhau. Nguyên nhân quan sát được: batch gọi `_stock_lots(..., cas_cap=EXACT_LOT_SCAN_CAP)` (50.000 lô) còn `/products` dùng mặc định `SAME_CAS_LOT_SCAN_CAP` (5.000 lô); ngoài ra trần 50.000 của batch dùng CHUNG cho mọi mã/CAS của cả lời gọi (500 mục) nên ở chiều ngược lại cũng có thể lệch khi tổng số lô lớn. Hợp đồng yêu cầu "nội dung JSON y hệt … `same_cas`".
- Mức ảnh hưởng thực tế hiện nay: staging chỉ có 763 lô tồn kho đang hoạt động (CAS nhiều lô nhất: 3 lô) nên không thể xảy ra trên dữ liệu staging; chỉ tái hiện được bằng dữ liệu dựng.
Negative case đã thử: như trên.
Residual risk: mẫu staging không có sản phẩm còn hàng (IN_STOCK) — tồn kho còn hàng chỉ được so trên dữ liệu thử local.

Classification: IMPLEMENTATION_FAIL
Reproduction: `tests.independent.test_machine_api_batch_independent.B4_Parity.test_parity_same_cas_when_cas_has_more_than_5000_lots` (thông báo `OBSERVATION[B4-internal]`).
Suggested area: tập lô đầu vào mà batch đưa cho hàm dựng kết quả dùng chung (`_stock_lots` trong `products_batch`).

## Claim B5 — Quyền và bảo mật giữ nguyên

Test thực hiện: `B5_Security` — 6 thẻ khách × 3 tổ hợp `include`, kèm header `X-Profile: internal`, query `?profile=internal&include_price=1`, `max_per_item=50`; quét khóa cấm và giá trị canh (giá, ghi chú, giá kho, ghi chú kho); hãng trong kết quả và trong `same_cas` so với `team_brands`; team không VIEW_CODE, team không VIEW_CAS, team có SEARCH_BY_CAS+VIEW_CAS nhưng KHÔNG có SEARCH; khóa cấp ngoài lạ (`profile`, `include_price`, `limit`, `team_id`, `Items`, `fields`), `include`/`max_per_item` sai (null, chuỗi, 0, âm, 2.0, bool, danh sách, Infinity), `items` null/{}/chuỗi/rỗng/501; không thẻ, sai thẻ, IP lạ (2 kiểu), thiếu DSN nội bộ; GET/PUT/DELETE/PATCH; > 128 KB; POST vào `/products`,`/compliance`,`/stock`; thẻ 3 lần/phút gọi 3 batch × 420 mục rồi lượt thứ 4; giao dịch chỉ-đọc.
Kết quả: PASS
Evidence: thẻ khách: 0 khóa/giá trị cấm ở mọi tổ hợp, `max_per_item` luôn 10, chỉ hãng của team (kể cả `same_cas`), header/query lạ bị bỏ qua; khóa lạ → 400 không có `results`; mục `code` thiếu VIEW_CODE và mục `cas` thiếu VIEW_CAS → `NOT_ALLOWED`, `results=[]`, trường bị ẩn đúng như `/products` (code/size/cas/brand, compliance `HIDDEN`, không `same_cas`); 401/401/403/403/503/405×4/413/405×3 đúng; 3 batch lớn = 3 lượt, lượt 4 → 429; `transaction_read_only=on`. Staging: thẻ khách 500 mã → không có khóa giá; thẻ nội bộ có `price`; GET → 405, không thẻ → 401.
Negative case đã thử: như trên.
Ghi chú (SPEC_AMBIGUOUS, không dùng để kết luận FAIL): team có SEARCH_BY_CAS + VIEW_CAS nhưng không có SEARCH → cả lời gọi 403 (kể cả mục `cas`), thay vì mục `cas` được tra và mục `code` → `NOT_ALLOWED`. Cách này chặt hơn (giống `/products` vốn đòi SEARCH) và không lộ dữ liệu; hợp đồng không nói đường batch có đòi SEARCH cho mục `cas` hay không.
Residual risk: thử 413 với thân chunked không có Content-Length chỉ làm được qua Nginx (Nginx chặn, xem B8); test client của Werkzeug không mô phỏng được.

## Claim B6 — Nhật ký không rò bí mật

Test thực hiện: `B6_Logs` — bắt MỌI logger (root, DEBUG, kèm traceback) trong 8 lời gọi: 200 nội bộ, 200 khách, 400 include lạ, 400 > 500 mục, 413, 500 do ký tự lạ, 400 too_many_results, 503 do lỗi DB giả; dò mã canh, `ref` canh, thẻ, giá canh, CAS, DSN, `SELECT`, `unnest`; thân lỗi 4xx/5xx không chứa giá trị gửi lên/SQL/DSN/Traceback. Staging: sau các lời gọi thật, dò `ref` canh, `ref` thường, mã hàng, CAS `MIXTURE` và giá trị thẻ nội bộ trong `/var/log/nginx/*` và journal 20 phút của `search-tools-machine-api`.
Kết quả: PASS
Evidence: 8 dòng truy cập, mỗi dòng đúng 7 khóa `{ts, token, endpoint, status, results, ip, ms}`, `endpoint=products_batch`; không có giá trị nào bị dò (trừ thông điệp do chính test giả lập lỗi DB đưa vào). Staging: 0 lần xuất hiện ở cả Nginx lẫn journal; dòng Nginx dạng `"POST /machine/v1/products/batch" 200 644215 2.718` (không query, không thân, không thẻ); journal chỉ có dòng JSON 7 khóa.
Negative case đã thử: như trên, gồm cả lỗi 500 không lường trước (traceback `UnicodeEncodeError` không chứa giá trị mục).
Residual risk: nhật ký lỗi DB (`audit_log.exception`) in thông điệp của psycopg2; với lỗi thật có vị trí (`LINE n: …`) PostgreSQL có thể trích đoạn câu lệnh đã ghép tham số — có từ trước batch, không tái hiện được bằng lỗi thật trong lượt này.

## Claim B7 — Nhanh, dùng chỉ mục, không ảnh hưởng ứng dụng nhân viên

Test thực hiện (staging, 1.262.173 sản phẩm, qua Nginx + TLS từ chính máy chủ): 500 mã ngẫu nhiên có thật (TABLESAMPLE) đủ `include`, thẻ nội bộ, 2 lần; 500 CAS phổ biến nhất (đứng đầu `MIXTURE` 3.419 sản phẩm) đủ `include` bằng thẻ nội bộ và thẻ khách (team 166, 5 hãng); thân 100 KB; `/login` nhân viên gọi mỗi 0,2–0,3 giây trong lúc chạy; `EXPLAIN (ANALYZE, BUFFERS)` câu SQL đúng như ứng dụng sinh ra (lấy nguyên văn từ `_batch_rows`), chạy chỉ-đọc.
Kết quả: **FAIL**
Evidence:
- Đạt: 500 mã, thẻ nội bộ: 0,315 s và 0,325 s (559 kết quả, 572 KB); thẻ khách 500 mã: 0,21 s; 500 CAS phổ biến, thẻ nội bộ: 3,81 s, 200, 10.000 kết quả (vừa đúng trần, không 400). Kế hoạch: `Index Scan using idx_products_code_upper_trim` (198 ms) và `Bitmap Index Scan on idx_products_cas_upper_trim` (779 ms), không Seq Scan trên `products`. `/login`: 200 trước, trong (2 + 13 + 2 lần) và sau; trong lúc lời gọi khách bị treo 8 giây, 21/21 lần `/login` = 200, chậm nhất 0,11 s.
- **Không đạt**: 500 CAS phổ biến nhất với **thẻ khách** → **HTTP 503 `service_busy` sau 8,1 s**, lặp lại 2/2 lần (journal: `"token": "smoke-batch-khach", "status": 503, "ms": 8030`). 100 CAS đầu với cùng thẻ: 200 sau 2,82 s. EXPLAIN bản khách: 11,46 s; mỗi lượt CAS dùng `BitmapAnd` giữa `idx_products_cas_upper_trim` và `idx_products_brand` — `Bitmap Index Scan on idx_products_brand (rows=102968 loops=2500)`. Hợp đồng: "500 CAS phổ biến nhất không lỗi 5xx (hoặc 400 `too_many_results` nếu vượt trần)" — câu này không giới hạn loại thẻ.
Negative case đã thử: như trên (thẻ khách + CAS phổ biến là ca nặng nhất).
Residual risk: chưa đo đồng thời nhiều lời gọi batch (Gunicorn của cổng máy: 1 worker × 8 luồng, timeout 15 s); mỗi lời gọi khách như trên giữ DB ~8 s.

Classification: IMPLEMENTATION_FAIL
Reproduction: trên staging, thẻ khách `/root/.sb_customer.tok`, POST `/machine/v1/products/batch` thân `{"items":[{"cas": c} for c in 500 CAS nhiều sản phẩm nhất (UPPER(TRIM(cas)) GROUP BY … ORDER BY count DESC)], "include":["compliance","stock"]}` → 503 sau ~8 s.
Suggested area: kế hoạch truy vấn mục `cas` khi có điều kiện giới hạn hãng của team trong `_batch_rows`.

## Claim B8 — Nginx chỉ nới đúng đường batch

Test thực hiện (staging): `nginx -t`; `nginx -T` xác nhận khối `location = /machine/v1/products/batch` đang nạp; thân JSON hợp lệ đúng 102.400 byte tới batch; thân 100 KB tới `/machine/v1/products` và `/machine/v1/compliance`; thân 204.800 byte tới batch; `/login` trước/sau; dò thân yêu cầu trong log Nginx; xem `log_format`.
Kết quả: PASS
Evidence: `nginx -t` "syntax is ok / test is successful"; batch 100 KB → 200 (500 mục, đúng thứ tự); `/products` 100 KB → 413 trang HTML của nginx/1.24.0; `/compliance` 100 KB → 413 (nginx); batch 200 KB → 413 (nginx); khối cha vẫn `client_max_body_size 8k`; `log_format machine_api "$remote_addr [$time_local] \"$request_method $uri\" $status $body_bytes_sent $request_time"` — không có thân/query/thẻ; `nginx -T` không có `$request_body`; `ref` canh không có trong `/var/log/nginx/*`; `/login` = 200 (lần reload duy nhất trong ngày: 15:21:17, sau khi snippet sửa lúc 15:21:16).
Negative case đã thử: thân lớn tới đường không phải batch, thân vượt 128 KB tới batch.
Residual risk: verifier không được reload Nginx nên "trước reload" chỉ suy ra từ nhật ký systemd, không tự quan sát.

## Claim B9 — Chạy thật qua n8n (UAT staging)

Kết quả: NOT RUN (UAT sau) — ngoài phạm vi lượt này theo yêu cầu.

## Task status

attempt: 1 / max_auto_repairs: 2  (agent chính: lượt 1 FAIL B2, B4, B7 → sửa lần 1, 2026-11-26)
same_claim_repeat_fail: none
next_action: CODER_REPAIR
