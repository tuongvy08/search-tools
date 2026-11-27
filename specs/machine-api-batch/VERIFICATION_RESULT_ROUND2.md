# Verification Result — machine-api-batch (lượt 2)

Lượt 1 (FAIL B2, B4, B7): [VERIFICATION_RESULT_ROUND1.md](VERIFICATION_RESULT_ROUND1.md). Verifier lượt 2 ghi kết quả bên dưới.

## Task status

attempt: 2 / max_auto_repairs: 2  (lượt 1 FAIL B2, B4, B7 → sửa lần 1; lượt 2 FAIL B4 → escalate, PO 2026-11-27 duyệt hợp đồng v2 + sửa lần 2)
same_claim_repeat_fail: B4 (lượt 1 và lượt 2 liên tiếp)
next_action: ESCALATE_USER

Verifier lượt 2 (2026-11-26, ngữ cảnh sạch). Mã kiểm: worktree `feature/machine-api-batch` (chưa commit), `machine_api.py` sha256 `05700e7c…f0c9b3` — trùng với bản đang chạy trên staging (`/srv/search-tools-machine-api/app/machine_api.py`, dịch vụ khởi động 15:44 UTC). Test độc lập mới: `tests/independent/test_machine_api_batch_round2.py`; test lượt 1 (`test_machine_api_batch_independent.py`) chạy lại nguyên trạng.

Lệnh đã chạy (DATABASE_URL local, PYTHONPATH=.:tests:tests/independent):
- `unittest tests.test_machine_api tests.test_machine_api_internal tests.test_machine_api_batch` → **Ran 106, OK**.
- `unittest discover -s tests/independent -p "test_machine_api*.py"` → **Ran 272, OK** (271,7 s; gồm toàn bộ test lượt 1, kể cả 2 test lone surrogate và test same_cas 5005 lô từng FAIL).
- `unittest tests.independent.test_machine_api_batch_round2` → Ran 11: 7 PASS, **4 FAIL (đều thuộc B4)**.

Kết luận nhanh: B1 PASS · B2 PASS · B3 PASS · **B4 FAIL (IMPLEMENTATION_FAIL; kèm một phát hiện SPEC_AMBIGUOUS)** · B5 PASS · B6 PASS · B7 PASS · B8 PASS · B9 NOT RUN (UAT sau). B4 FAIL lượt thứ hai liên tiếp (lượt 1 cũng FAIL B4, nguyên nhân quan sát khác) — agent chính cập nhật `same_claim_repeat_fail` theo quy trình.

## Claim B1 — Không hồi quy

Test thực hiện: chạy 3 bộ test của agent chính + toàn bộ `tests/independent/test_machine_api*.py`; đếm số test so với `origin/main`; `B1_OldVsNew` (lượt 1) chạy bản `machine_api.py` của `origin/main` trong tiến trình riêng và so từng phản hồi GET `/products`, `/compliance`, `/stock` với bản mới trên cùng dữ liệu; `git diff origin/main --stat`.
Kết quả: PASS
Evidence: 106/106 OK; 272/272 OK. Số test: `test_machine_api.py` 52 → 52, `test_machine_api_internal.py` 33 → 33 (không file test cũ nào bị sửa), thêm `test_machine_api_batch.py` 21. Hai test độc lập cũ đổi đúng như Change request log (chỉ danh sách đường trong openapi). Diff so `origin/main` chỉ chạm `machine_api.py`, `deploy/nginx-machine-api.conf.example`, tài liệu, test; không chạm `search.py`, `app.py` hay middleware ứng dụng nhân viên.
Negative case đã thử: so GET cũ/mới trên dữ liệu có ca biên (CAS có 5005 lô của mã khác, mã chứa khoảng trắng/tab, ký tự `% _ !`, 7 thẻ quyền khác nhau) — 0 khác biệt (test lượt 1, chạy lại PASS). Bộ dữ liệu mới của lượt 2 (50 000 lô một mã, hai CAS cho một mã) không so cũ/mới; `/products` trên dữ liệu này chỉ dùng làm đối chứng cho B4.
Residual risk: so cũ/mới chỉ trên dữ liệu thử; không so trên dữ liệu staging.

## Claim B2 — Đủ mục, đúng thứ tự, trạng thái tường minh

Test thực hiện: chạy lại 2 test FAIL lượt 1 (lone surrogate trong `code` và trong `ref`) + 8 biến thể surrogate mới (`\udfff` trong `cas`, surrogate giữa khoảng trắng, ở đầu chuỗi, trong TÊN khóa của mục, trong danh sách, trong `ref` dạng đối tượng, trong khóa cấp ngoài, trong `include`); ký tự astral hợp lệ (emoji) trong `ref`/`code`; NBSP/khoảng trắng chữ Hán bao quanh; ký tự C1 giữa chuỗi và cuối chuỗi; thân hỏng (lồng `[`×50 000 ở cấp ngoài và trong `items`, byte không phải UTF-8, BOM, `1e400`, `NaN`, số 5000 chữ số).
Kết quả: PASS
Evidence: cả 2 test lượt 1 PASS. 8/8 biến thể surrogate → HTTP 200, chỉ mục chứa surrogate là `INVALID` có `message`, mục hợp lệ trước/sau vẫn `FOUND`; phản hồi không chứa `\ud8…`. Surrogate trong khóa cấp ngoài / `include` → 400 sạch. Emoji trong `ref` trả lại nguyên văn; `{"code":"😀"}` → NOT_FOUND. `VX\u0085100`, `VX\u009f100` → INVALID (lượt 1 là NOT_FOUND). Thân lồng sâu → 400 (lượt 1 là 500); byte không phải UTF-8 → 400; `max_per_item: 1e400` → 400; `NaN`/số rất lớn → 200, không 5xx. Test lượt 1 về 500 mục trùng/500 mục sai/mục là số-null-danh sách PASS.
Negative case đã thử: như trên; không ca nào làm hỏng cả lời gọi hay trả 5xx.
Residual risk: ký tự điều khiển ở ĐẦU/CUỐI chuỗi mà Python coi là khoảng trắng (`\x1c`–`\x1f`, `\x85`, tab, xuống dòng) bị bỏ như khoảng trắng nên `"VX-100\x1f"`, `"VX-100\u0085"` → FOUND thay vì INVALID. Hợp đồng vừa cho "bỏ khoảng trắng đầu/cuối" vừa đòi "ký tự điều khiển → INVALID"; ghi nhận, không kết luận FAIL.

## Claim B3 — Khớp CHÍNH XÁC, trả MỌI sản phẩm khớp, không tự chọn

Test thực hiện: test lượt 1 (đối chiếu `matches_total` với SQL độc lập `UPPER(TRIM(..)) = UPPER(..)`, mã chỉ "chứa"/tiền tố/tab không được trả, `% _ !` là ký tự thường, 28 sản phẩm 7 hãng một mã bị cắt ở 3/5/7/10 → số hãng ≥ min(số hãng, max_per_item), lặp 3 lần cho kết quả y hệt, trùng mã khác hãng/quy cách trả tất cả); trên staging: 500 mã ngẫu nhiên và 500 CAS phổ biến.
Kết quả: PASS
Evidence: các test lượt 1 PASS. Staging: 500 mã ngẫu nhiên → 500 FOUND / 569 sản phẩm (có mã khớp nhiều sản phẩm, không tự chọn một); 500 CAS phổ biến, thẻ nội bộ → 500 FOUND, 500 `truncated`, đúng 20 kết quả/mục.
Negative case đã thử: `VX-10` (tiền tố của mã có thật) → NOT_FOUND; `22-22-2` (chuỗi con của CAS) → NOT_FOUND; `%`, `_`, `U%` → NOT_FOUND; NBSP/khoảng trắng chữ Hán bao quanh mã → FOUND (bỏ như khoảng trắng).
Residual risk: hoa/thường ngoài ASCII phụ thuộc collation PostgreSQL (hai vế cùng dùng UPPER nên nhất quán; chưa thử chữ có dấu).

## Claim B4 — Trùng khớp với `/products` (giá, pháp chế, tồn kho)

Test thực hiện: test lượt 1 (≥ 60 sản phẩm × 5 thẻ, gồm ca 5005 lô cùng CAS) + 5 ca biên mới trong `R2_B4_LotWindows`: so từng sản phẩm batch với phần tử cùng tên/mã/hãng/quy cách/CAS trong `/products?q=<mã>` (cùng limit).
Kết quả: FAIL
Evidence:
- **(a) Tồn kho của một mục phụ thuộc vào các mục khác trong cùng lời gọi.** Dữ liệu: mã `BIGLOT-A` (hãng ALPHA) có 50 000 lô (= `EXACT_LOT_SCAN_CAP`), mã `ZLOT-B` (hãng ZETA) có 3 lô còn hàng. Thẻ nội bộ, `include=["stock"]`: batch chỉ `[ZLOT-B]` → `IN_STOCK`, `lots_total=3`; batch `[BIGLOT-A, ZLOT-B]` → ZLOT-B `OUT_OF_STOCK`, `quantity=0`, `lots=[]`, `lots_total=0`, `lots_truncated=false`; `/products?q=ZLOT-B` → `IN_STOCK`, quantity 15, 3 lô. Tức là trần lô theo mã dùng chung cho mọi mã trong gói, và khi vượt trần thì mã xếp sau bị báo hết hàng mà không có cờ cắt nào.
- (b) Ca same_cas khi chính `/products` có cửa sổ lô phụ thuộc trang kết quả (xem "Phát hiện thêm" bên dưới): `CCC-ASK` (CAS 777-77-7, 5005 lô `AAA-ONE` + 1 lô `CCC-ASK-X`; `/products?q=CCC-ASK` trả thêm sản phẩm `CCC-ASK-X` theo kiểu "chứa") → batch `same_cas=[AAA-ONE]`, `/products?q=CCC-ASK&limit=50` `same_cas=[AAA-ONE, CCC-ASK-X]` (thẻ internal, cust, wide; cả khi tra theo mục `cas`). `DUAL-1` (hai sản phẩm cùng mã, CAS 881-11-1/ALPHA và 882-22-2/ZETA; 5001 lô ALPHA của CAS thứ nhất, 1 lô ZETA của CAS thứ hai) → sản phẩm ZETA: batch `same_cas=[ZZZ-882]`, `/products?q=DUAL-1` `same_cas=[]`.
- Ngoài các ca trên: lô của mỗi mục khi đứng riêng khớp `/products`; trộn 6 mục (`cas` 777-77-7, 881-11-1, 222-22-2; `code` VX-100, MULTI-1, PAR-001) với thẻ internal/cust/wide: 41/18/23 sản phẩm so, chỉ lệch đúng `CCC-ASK` của ca (b).
Negative case đã thử: (a), (b) như trên; test lượt 1 về 5005 lô của một CAS (CCC-ASK + BBB-TWO, không có mã "chứa") nay PASS.
Residual risk: staging hiện chỉ có **763 lô tồn kho đang hoạt động** (đếm chỉ đọc), nên với dữ liệu hiện tại không chạm được trần 50 000 lô/gói của (a) hay 5000 lô/CAS của (b); chưa biết khối lượng tồn kho production (không truy cập). Giá và pháp chế không thấy lệch trong mọi ca đã so.
Classification: IMPLEMENTATION_FAIL (theo (a): `stock.status/quantity/lots` của một sản phẩm sai so với `/products` và thay đổi theo các mục khác trong gói, không có cờ báo cắt)
Reproduction: `tests.independent.test_machine_api_batch_round2.R2_B4_LotWindows.test_code_lot_cap_is_not_shared_across_batch` (dòng OBSERVATION[R2-B4-codecap] in ba trạng thái). Ca (b): `…test_contains_match_code_in_products_page`, `…test_same_code_two_cas_values`, `…test_cas_item_and_mixed_batch_parity`.
Suggested area (tùy chọn): cửa sổ đọc lô tồn kho theo mã khi gom nhiều mục trong một gói.

### Phát hiện thêm trong B4 — same_cas (Classification: SPEC_AMBIGUOUS)

Observed: chính `/products` trả `same_cas` khác nhau cho CÙNG một sản phẩm tùy trang kết quả: `/products?q=CCC-ASK&limit=1` → `same_cas=[AAA-ONE]`; `/products?q=CCC-ASK&limit=50` → `same_cas=[AAA-ONE, CCC-ASK-X]` (`test_products_itself_depends_on_page_context`). Lý do quan sát được: cửa sổ 5000 lô cùng CAS của `/products` bỏ ra mọi mã có mặt trong trang và dùng chung trần cho mọi CAS trong trang, nên kết quả phụ thuộc vào các sản phẩm khác mà `q` tình cờ khớp (kể cả khớp "chứa"). Batch trả theo từng sản phẩm, bằng với `/products` khi trang chỉ có mã đó (limit=1).
Expected theo hợp đồng: "nội dung JSON y hệt `/products?q=<mã>` … kể cả `same_cas`". Khi `/products` tự nó không cho một đáp án duy nhất cho cùng sản phẩm, chưa rõ batch phải tái tạo đúng hiệu ứng của trang `/products?q=<mã>` (gồm các sản phẩm khớp "chứa", theo `limit` nào) hay chỉ cần trùng khi trang `/products` chỉ có đúng sản phẩm đó. Cần chủ dự án/hợp đồng chốt; verifier không tự chọn cách hiểu.
Reproduction: các test (b) ở trên + `test_products_itself_depends_on_page_context`.

## Claim B5 — Quyền và bảo mật giữ nguyên

Test thực hiện: test lượt 1 (6 thẻ khách × 3 tổ hợp include, kèm header/query nâng quyền; quyền trường; NOT_ALLOWED; khóa cấp ngoài lạ; 401/403/503/405/413; 1 lời gọi = 1 lượt tần suất; giao dịch chỉ-đọc); mới: gợi ý cùng CAS cho thẻ khách khi CAS có lô của hãng ngoài team (`VISX-1`/CAS 990-00-1 có lô ALPHA và ZETA; `DUAL-1`; mục `cas` 882-22-2 chỉ có sản phẩm ZETA) với thẻ `cust` (ALPHA, BETA) và `limited`; staging: quét khóa phản hồi thẻ khách.
Kết quả: PASS
Evidence: test lượt 1 PASS. Thẻ `cust`/`limited`: phản hồi không chứa chữ `ZETA` hay `ZZZ-882`; mục `cas` 882-22-2 → NOT_FOUND (cust) / NOT_ALLOWED (limited, không có quyền CAS). Staging, thẻ khách (team 166), 500 CAS phổ biến + 500 mã, đủ include: không có khóa nào trong {price, note, lots, stock_price_vnd, stock_note, unit_price_vnd, ship_factor, expiry_date}; thẻ nội bộ có `price`. GET tới đường batch trên staging → 405.
Negative case đã thử: như trên.
Residual risk: team không có SEARCH nhưng có SEARCH_BY_CAS+VIEW_CAS bị từ chối cả lời gọi (test lượt 1 chấp nhận 200 hoặc 403) — cùng hành vi với `/products`; ghi nhận.

## Claim B6 — Nhật ký không rò bí mật

Test thực hiện: test lượt 1 (bắt log truy cập/lỗi, thân lỗi không lặp lại giá trị); staging: gửi 500 mục có `ref` mang chuỗi đánh dấu `VB8SENTINEL…` và mã `QQQ…` rồi tìm trong log Nginx và journal dịch vụ.
Kết quả: PASS
Evidence: test lượt 1 PASS. Staging: chuỗi đánh dấu xuất hiện 0 lần trong `machine_api.access.log`, `machine_api.error.log`, `access.log`, `error.log` và journal `search-tools-machine-api`. Dòng log ứng dụng chỉ có `ts, token (id), endpoint=products_batch, status, results, ip, ms`. `log_format machine_api` chỉ ghi `$remote_addr`, thời gian, method, `$uri` (không query), status, số byte, thời gian.
Negative case đã thử: surrogate/thân hỏng (B2) không còn sinh 5xx nên không đi vào nhánh ghi traceback.
Residual risk: nhánh `audit_log.exception` (lỗi DB/lỗi bất ngờ) vẫn ghi traceback; không tìm được đầu vào batch nào kích hoạt nó.

## Claim B7 — Nhanh, dùng chỉ mục, không ảnh hưởng ứng dụng nhân viên

Test thực hiện (staging, qua Nginx+TLS thật từ 127.0.0.1, dữ liệu 1 262 173 sản phẩm): 2 bộ 500 mã ngẫu nhiên có thật (ORDER BY random()), 500 CAS nhiều sản phẩm nhất (tổng 32 194 sản phẩm, nhiều nhất 3 419/CAS), đủ `include`, thẻ nội bộ và thẻ khách; `/login` gọi mỗi 0,5 s trong lúc chạy; `EXPLAIN (ANALYZE, BUFFERS)` của đúng SQL batch (lấy từ `_batch_rows`) với 500 mã và 500 CAS đó, cả bản nội bộ và bản khách (team 166), phiên chỉ-đọc.
Kết quả: PASS
Evidence:
- Nội bộ, 500 mã: HTTP 200 trong 0,44 s / 0,28 s (cùng bộ, 2 lần) và 0,40 s (bộ khác); 500 FOUND, vị trí và ref đúng thứ tự.
- Nội bộ, 500 CAS phổ biến: HTTP 200, 3,82 s và 3,81 s; 10 000 kết quả (đúng trần, không vượt), 9,2 MB.
- Khách, 500 CAS phổ biến: HTTP 200, 1,43 s và 1,50 s (lượt 1: 503 sau 8,1 s); khách 500 mã: 200, 0,21 s.
- `/login` trong lúc chạy: 51/51 lần 200, chậm nhất 0,10 s; trước và sau: 200.
- EXPLAIN: mã → `Index Scan using idx_products_code_upper_trim on products` (500 vòng), 40 ms (nội bộ) / 48 ms (khách); CAS → `Bitmap Index Scan on idx_products_cas_upper_trim` (500 vòng), 741 ms (nội bộ) / 474 ms (khách; lượt 1: 11,46 s). Không có Seq Scan trên `products` (chỉ Seq Scan trên bảng nhỏ `teams`, `team_brands`, `brand_compliance_settings`).
Negative case đã thử: 500 CAS phổ biến nhất với thẻ khách (ca FAIL lượt 1), 2/2 lần.
Residual risk: chạy lúc staging rảnh, từng lời gọi một; chưa đo nhiều lời gọi batch đồng thời (dịch vụ 1 worker × 8 thread). Không tách riêng EXPLAIN cho truy vấn lô tồn kho (bảng tồn kho staging chỉ 763 lô đang hoạt động).

## Claim B8 — Nginx chỉ nới đúng đường batch

Test thực hiện (staging): thân 100 KB / 127 KB / 129 KB tới `/machine/v1/products/batch`; thân 100 KB (POST và GET) tới `/products`, `/products?q=abc`, `/compliance`, `/stock` và 10 biến thể đường dẫn (`/products/batch/`, `/products/batch/.`, `/products/./batch`, `/products/%62atch`, `/products%2Fbatch`, `/products/batch%2F..%2F`, `/products/batch/../batch`, `/./products/batch`, `/products/batch;x`, `/PRODUCTS/BATCH`); `nginx -t`; đọc cấu hình `/etc/nginx/snippets/machine-api-location.conf` và `log_format`.
Kết quả: PASS
Evidence: 100 KB và 127 KB tới batch → 200 (phản hồi JSON của batch); 129 KB → 413 trang HTML của nginx. 100 KB tới `/products`, `/compliance`, `/stock` (POST và GET) → 413 từ nginx. Các biến thể Nginx chuẩn hóa về đúng `/machine/v1/products/batch` (`/./`, `%62`, `%2F`, `/../batch`) → POST 200 và log ứng dụng ghi `endpoint=products_batch` (chỉ tới được batch), GET 405; các biến thể còn lại → 413. `nginx -t`: "syntax is ok … test is successful". Cấu hình chỉ có `location = /machine/v1/products/batch { client_max_body_size 128k; client_body_buffer_size 128k; proxy_pass … }` lồng trong `location /machine/` (giữ 8k cho phần còn lại). Log không chứa thân (B6). `/login` 200 trước và sau lượt kiểm.
Negative case đã thử: các biến thể đường dẫn trên.
Residual risk: không quan sát được lúc reload (đã reload trước lượt này; tiến trình master khởi động 21/11, reload giữ nguyên master); verifier không reload.

## Claim B9 — Chạy thật qua n8n (UAT staging)

Kết quả: NOT RUN (UAT sau) — ngoài phạm vi lượt này.

## Cross-vendor review (Codex chỉ-đọc, 2026-11-26, do agent chính ghi)

Phạm vi gửi: bản xuất sạch các file đã theo dõi + file mới (không `.env`, không dữ liệu thật), `BATCH_CHANGE.diff`, hợp đồng B1..B8; chạy `codex exec --sandbox read-only --ephemeral`. Kết luận của Codex: chưa thấy rò giá/ghi chú cho thẻ khách, lọt hãng ngoài team hay SQL injection; Nginx khối con kế thừa đúng `limit_req`/`proxy_set_header`. Phát hiện:

1. Chặn (B4): trần 50.000 lô theo mã dùng chung cho cả gói → mã cuối có thể thành OUT_OF_STOCK sai khi tổng lô > 50.000 (trùng phát hiện verifier lượt 2).
2. Chặn (B4): cửa sổ `same_cas` của batch khác `/products` khi trang kết quả `/products` có thêm mã "chứa" từ khóa cùng CAS với > 5.000 lô (verifier lượt 2 xếp SPEC_AMBIGUOUS: chính `/products` cho kết quả khác nhau tùy `limit`).
3. Nên sửa (B5): team thiếu SEARCH nhưng có SEARCH_BY_CAS+VIEW_CAS → 403 cả gói. Agent chính giữ nguyên: cùng yêu cầu SEARCH như `/products` ("quyền áp dụng như /products"), chặt hơn, không lộ dữ liệu.
4. Nên sửa (B6): đường ghi lỗi DB có sẵn (`audit_log.exception`) có thể ghi thông điệp lỗi; test độc lập lượt 1 lọc bỏ bản ghi "boom". Có từ trước (dùng chung với `/products`), ghi nhận ngoài phạm vi, không sửa trong task này.
