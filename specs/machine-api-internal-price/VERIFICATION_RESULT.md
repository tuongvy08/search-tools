# Verification Result — machine-api-internal-price — LƯỢT 4 (claim I1..I8; I9 do bên khác chạy, KHÔNG kiểm ở đây)

Do verifier độc lập chạy lại (2026-11-22), ngữ cảnh sạch: chỉ đọc VERIFICATION.md đã khóa (kể cả "Change request log", mục mới nhất "sửa lần 3"), diff `git diff 351cf1a -- machine_api.py` (phần truy vấn lô của `_stock_lots`) và tự chạy. Mã ứng dụng (`machine_api.py`) không bị sửa. Kết quả lượt 1-3 bị ghi đè bằng tài liệu này.

Test độc lập của verifier:

- `tests/independent/test_machine_api_internal_independent.py` — 56 test (lượt 1, giữ nguyên).
- `tests/independent/test_machine_api_internal_round2.py` — 31 test (lượt 2, giữ nguyên).
- `tests/independent/test_machine_api_internal_round3.py` — 18 test (lượt 3; lượt 4 CHỈNH test T2/T2b, xem mục cuối).
- `tests/independent/test_machine_api_internal_round4.py` — 9 test MỚI lượt 4: so thẻ khách với bản cũ `351cf1a` (nhiều mã/nhiều hãng/lọc hãng, > 7.200 lô cùng CAS, 3 kiểu `CLUSTER`), biên cắt 5.000 có chứa lô của mã đang hỏi, thẻ nội bộ bất biến theo thứ tự vật lý và khớp oracle (kể cả 50.050 lô một mã), hiệu năng (EXPLAIN ANALYZE có/không ORDER BY trong nhánh; HTTP; 300.000 lô; một CAS 300.000 lô).
- `tests/independent/machine_api_internal_old_runner.py` — trợ giúp chạy bản cũ `351cf1a` đọc từ Git.

Chạy lại:

```
export DATABASE_URL=postgresql://searchlocal:searchlocal@localhost:5432/products_local
export PYTHONPATH=.:tests:tests/independent
PY=/Volumes/DATA/Development/Search-tools/.venv/bin/python
for m in test_machine_api_internal_independent test_machine_api_internal_round2 test_machine_api_internal_round3 test_machine_api_internal_round4 \
         test_machine_api_independent test_machine_api_round2 test_machine_api_round3 test_machine_api test_machine_api_internal; do $PY -m unittest $m; done
```

Kết quả lượt này (cùng một cây mã, chưa commit): **333/333 đạt, 0 fail** — bộ nội bộ lượt 1 **56/56**; lượt 2 **31/31**; lượt 3 **18/18**; lượt 4 **9/9**; bộ khách độc lập `test_machine_api_independent` **87/87**, `round2` **25/25**, `round3` **22/22**; bộ của nhà phát triển `test_machine_api` **52/52** (không giảm so với `351cf1a`), `test_machine_api_internal` **33/33**.

## Tóm tắt kết luận

| Claim | Kết quả |
| --- | --- |
| I1 | **PASS** (hồi quy `same_cas` đã hết; lô cùng CAS > 5.000 không còn phụ thuộc thứ tự vật lý) |
| I2 | PASS |
| I3 | PASS (ghi nhận SPEC_AMBIGUOUS/VERIFICATION_CONTRACT_PROBLEM như lượt 3) |
| I4 | PASS |
| I5 | PASS |
| I6 | PASS |
| I7 | PASS |
| I8 | PASS |
| I9 | không kiểm |

## Đọc diff của bản sửa

Mỗi nhánh của truy vấn trong `_stock_lots` nay là `(SELECT ... WHERE ... ORDER BY i.brand_norm, i.code_norm, i.size_norm, i.expiry_date NULLS LAST LIMIT N)`: nhánh đúng mã `LIMIT 50000`, nhánh cùng CAS (loại các mã đang hỏi) `LIMIT 5000` (hoặc 50000 khi tra `/stock?cas=`), hai nhánh `UNION ALL` rồi `ORDER BY` ngoài cùng như cũ. Khóa sắp xếp là duy nhất trong một snapshot (chỉ mục duy nhất `(snapshot_id, brand_norm, code_norm, size_norm, expiry_date)` và biến thể `expiry_date IS NULL`), nên tập lô giữ lại hoàn toàn tất định, không có hòa. Không có thay đổi nào khác trong truy vấn (không chọn thêm cột giá cho thẻ khách; cột giá/ghi chú vẫn chỉ ở `extra_cols` của thẻ nội bộ). Khác biệt ngữ nghĩa nhỏ còn lại so với `351cf1a` (phân tích ở I1): bản cũ cắt 5.000 dòng CHUNG (gồm lô của mã đang hỏi nếu sắp trước), bản mới cắt 5.000 dòng cùng CAS KHÔNG gồm lô mã đang hỏi — cửa sổ gợi ý lớn hơn tối đa bằng số lô của mã hỏi; không đổi 5 nhóm đầu thẻ khách (đã đo, xem I1).

## Claim I1 — Thẻ khách không đổi, không bao giờ có giá
Test thực hiện: toàn bộ ca lượt 1-3 chạy lại và đạt (60+ lời gọi thẻ khách với mọi mẹo `profile=internal`/`include_price`/`X-Profile`; thẻ khách không bao giờ dùng DSN nội bộ; role khách bị từ chối đọc giá; so với `351cf1a` 186 + 96 + 21 lời gọi; luân phiên thẻ nội bộ/khách không rò `g.internal`; Gunicorn thật; OpenAPI công khai không lộ khóa giá/lô). MỚI lượt 4:
(a) Hai ca đã FAIL ở lượt 3 — `T2b.test_customer_same_cas_groups_match_old_when_late_rows` (nhóm `AA-*` sắp đầu nhưng nằm cuối vật lý sau 6.000 lô `ZZ-*`) và `.test_customer_same_cas_independent_of_physical_row_order` (CLUSTER theo mã giảm dần) — nay `same_cas` của bản mới = `AA-0, AA-1, AA-2, ZZ-00, ZZ-01` = bản cũ, trước VÀ sau CLUSTER (lượt 3 bản mới cho `ZZ-00..04` rồi `ZZ-10..14`).
(b) `U1_CustomerVsOld`: 3 thẻ khách (đủ quyền, hạn chế hãng chỉ CATO, `stock_detail=quantity`) × 15 lời gọi (`/stock?code=`, `/products?q=` theo 6 mã đang hỏi nằm đầu/giữa/cuối thứ tự sắp xếp và 1 mã hãng GAMMA ngoài mọi team, `q=Own` nhiều sản phẩm một truy vấn, `q=<CAS1>`, `q=<CAS2>`); dữ liệu: CAS1 = 7.200 lô của 144 mã × 4 hãng nhập xáo trộn, CAS2 = 5.600 lô nhập mã giảm dần. So `same_cas`/`same_cas_more` THEO TỪNG MÃ (thứ tự kết quả đổi có chủ ý theo I7) với bản cũ: **0 khác biệt/45 lời gọi** ở trạng thái ban đầu và sau mỗi trong 3 kiểu `CLUSTER` (mã+hãng giảm dần; số lượng/hạn giảm dần; hãng+quy cách+mã giảm dần); toàn bộ phản hồi thẻ khách của bản mới giống hệt từng byte giữa 4 bố cục vật lý.
(c) `U1b`: biên cắt 5.000 có chứa lô của mã đang hỏi (mã `A0-FIRST` 300 lô sắp ĐẦU + 108 nhóm × 49 lô cùng CAS): 5 nhóm đầu thẻ khách và `same_cas_more` khớp bản cũ; thẻ nội bộ 10 nhóm đầu khớp oracle và mỗi nhóm đủ 49 (không bị cắt giữa chừng).
(d) Thẻ khách + `?profile=internal` v.v. vẫn không có giá (bộ lượt 1-2 chạy lại, đạt).
Kết quả: **PASS**
Evidence: số liệu trên; `U1` in `same_cas_diffs: 0` cho 4 bố cục; T2b `diffs: []`.
Negative case đã thử: xem trên (hãng ngoài team xen lẫn; mã hỏi ở đầu/giữa/cuối; 3 kiểu CLUSTER; biên 5.000 cắt giữa nhóm).
Residual risk (không FAIL): khi mã hỏi sắp ĐẦU và có nhiều lô, cửa sổ same_cas của bản mới lớn hơn bản cũ bằng số lô của mã hỏi; chỉ ảnh hưởng nhóm nằm đúng ranh giới 5.000 (nhóm 6+ với thẻ khách giới hạn 5 nhóm; nhóm 11+ của thẻ nội bộ) — chưa thấy khác biệt ở 5 nhóm đầu trong mọi ca.

## Claim I2 — Thẻ nội bộ chỉ chạy khi cấu hình đủ và an toàn (fail-closed)
Test thực hiện: toàn bộ ca lượt 1-3 chạy lại (16 mục thẻ không an toàn, DSN thiếu/sai → 503, hết hạn/thu hồi → 401, IP lạ → 403, phương thức ngoài GET → 405, trùng id/băm, biên dải IPv4 `/16` và IPv6 `/48`, hạn dùng 90 ngày chính xác, công cụ cấp thẻ, Gunicorn thật). Không có thay đổi mã liên quan lượt này.
Kết quả: PASS
Evidence: 0 fail trên các bộ lượt 1-3.
Negative case đã thử: xem trên.
Residual risk (giữ nguyên): cổng tin `X-Forwarded-For` của 1 bước proxy (`ProxyFix x_for=1`) nên chỉ an toàn khi cổng không bị gọi trực tiếp (chỉ nghe `127.0.0.1:8100`, Nginx ghi đè `X-Forwarded-For`); hành vi có từ trước, nay bảo vệ thẻ có giá.

## Claim I3 — Thẻ nội bộ thấy tất cả hãng, tất cả trường cần cho báo giá
Test thực hiện: ca lượt 1-3 chạy lại và đạt (7 hãng, mọi trường, không khóa id, `OUT_OF_STOCK` + `lots=[]`, team hạn chế không ảnh hưởng, 0..5.200 lô một mã, 15.000 lô trong một truy vấn, 50 × 1.000 = 50.000 lô, 50.050 lô). MỚI lượt 4: (a) thẻ nội bộ trả CÙNG MỘT phản hồi (`/products` mã giữa 8.000 lô cùng CAS, `/products` mã 50.050 lô, `/stock?cas=`, `/stock?code=`) trước và sau 3 kiểu `CLUSTER` — bất biến từng byte (`U2`); (b) 10 nhóm `same_cas` nội bộ khớp oracle SQL theo (hãng, mã) và số lượng mã hỏi khớp oracle; (c) mã có 50.050 lô vượt trần 50.000: tập giữ lại nay là đúng 50.000 lô ĐẦU theo (hãng, mã, quy cách, hạn): `quantity=100004` = oracle SQL `ORDER BY ... LIMIT 50000`, `lots_total=50000`, độc lập thứ tự vật lý (trước đây có thể tùy ý); (d) danh sách `lots` trong một nhóm sắp theo hạn dùng tăng dần; (e) `/stock?cas=` thẻ khách có `stock_detail=quantity` trên 6.000+ lô: tổng `18020` = oracle SQL (bản cũ `14995` do cắt 5.000 — cải thiện có chủ ý, items/status/more giống bản cũ).
Kết quả: PASS
Evidence: xem trên.
Negative case đã thử: xem trên.
Residual risk (SPEC_AMBIGUOUS, HUMAN_REVIEW, không xếp FAIL): (1) trần 50.000 lô của chính mã đang hỏi vẫn cắt không có cờ riêng (`lots_truncated` chỉ phản ánh trần 200); nay tập bị cắt ít nhất là tất định. (2) Khi một CAS có > 5.000 lô của mã khác, `same_cas` nội bộ lấy các nhóm ĐẦU theo thứ tự chữ cái (tất định, khớp oracle) nhưng nhóm ở ranh giới có thể bị cắt giữa chừng nên số lượng nhóm đó thiếu — chỉ ảnh hưởng nhóm gợi ý ở ranh giới, không phải mã đang hỏi. VERIFICATION_CONTRACT_PROBLEM (chờ PO, như lượt 2-3): "từng lô = tối đa 200 lô" thu hẹp lời PO "agent có thể xem và lấy được tất cả"; cần PO xác nhận 200 là đủ cho báo giá.

### Phán xét độc lập về khác biệt `status` của MID-OWN (test T2b)
Bản cũ `351cf1a` trả `status=OUT_OF_STOCK` (không có `pack_confirmed`) cho `MID-OWN` dù mã đó có 3 lô, mỗi lô 2 đơn vị, hạn còn 100+ ngày; bản mới trả `IN_STOCK` + `pack_confirmed`. Oracle SQL: tổng số lượng còn hạn của `MID-OWN` = 6 > 0. Nguyên nhân bản cũ: truy vấn lô sắp theo hãng rồi cắt 5.000 dòng chung; hãng `CATO` (mã hỏi) sắp SAU `ALPHA` (6.015 lô cùng CAS) nên lô của mã hỏi bị cắt mất và kết luận HẾT HÀNG sai. **Kết luận: đây là cải thiện đúng (I3), KHÔNG phải hồi quy** — khớp mục "sửa lần 2" và "sửa lần 3" của change log (khác biệt có chủ ý duy nhất so với `351cf1a`). Cùng loại khác biệt (mã hỏi sắp sau, trạng thái đúng nhờ đọc đủ lô; `/stock?cas=` quantity đúng) được kiểm bằng oracle chứ không bỏ qua mù trong T2 (7 lần) và T2b (1 lần). Với nội dung `same_cas` (thứ bản sửa lần 3 nhắm tới) bản mới KHỚP bản cũ.

## Claim I4 — Giá VND khớp từng đồng với ứng dụng nhân viên
Test thực hiện: ma trận lượt 1-3 chạy lại (52 + 64 ca giá/ship hỏng, 11 cặp giá×ship, hãng thiếu tỷ giá, đổi tỷ giá giữa hai lời gọi, tỷ giá `NaN`/`Infinity`/`1e400`/`1e-400`, giá tồn kho `NaN`/`Infinity`, hạn `infinity`, 26 sản phẩm bẩn/sạch một truy vấn). Không thay đổi mã liên quan lượt này.
Kết quả: PASS
Evidence: 0 fail; JSON luôn chuẩn nghiêm ngặt (không `NaN`).
Negative case đã thử: xem trên.
Residual risk (không FAIL): `INVALID_RATE_DATA` là mã do cổng tự đặt (chỉ khi tỷ giá `NaN` ghi thẳng DB); giá quy đổi > 1e15 VND → `null` + `INVALID_PRICE_DATA` trong khi ứng dụng nhân viên trả số (vô nghĩa thực tế); giá rỗng/chữ/âm → `0` + `NO_PRICE_DATA` đúng như ứng dụng nhân viên — client phải đọc `price_status`.

## Claim I5 — Thẻ nội bộ không chạm được dữ liệu ngoài 3 loại cho phép
Test thực hiện: bộ lượt 1-3 chạy lại nguyên (áp `sql/machine_internal_role.sql` thật; kiểm kê quyền từ catalog; 28 câu đọc bị cấm, 21 câu ghi/DDL, tự tắt `read_only`, `statement_timeout`, 24 payload tiêm SQL × 2 thẻ, Gunicorn thật). Truy vấn tồn kho có ORDER BY trong nhánh chạy dưới role thật ở các test này và đạt (chỉ dùng cột `brand_norm`, `code_norm`, `size_norm`, `expiry_date` đã được cấp).
Kết quả: PASS
Evidence: đúng 8 bảng đọc được và đúng các cột đã cấp; mọi truy vấn cấm → `InsufficientPrivilege`.
Negative case đã thử: xem trên.
Residual risk: như lượt 2-3 (quyền `TEMP` mặc định của PUBLIC; PostgreSQL ≤ 14 PUBLIC có `CREATE` trên schema `public`, chưa thử PG14; biến libpq `PG*`).

## Claim I6 — Nhật ký và phản hồi lỗi không rò bí mật
Test thực hiện: bộ lượt 1-3 chạy lại (mọi bản ghi log kể cả traceback, 8 loại lời gọi, 10 loại phản hồi lỗi, lỗi 500 cố ý, Gunicorn thật).
Kết quả: PASS
Evidence: 0 canary/bí mật trong log và thân lỗi; dòng truy cập có khóa `endpoint, ip, ms, results, status, token, ts`.
Negative case đã thử: xem trên.
Residual risk (SPEC_AMBIGUOUS, HUMAN_REVIEW, giữ nguyên): oracle liệt kê "… và nhãn profile" nhưng dòng truy cập thực tế KHÔNG có trường `profile`; theo nghĩa "danh sách được phép" thì không vi phạm.

## Claim I7 — Xếp hạng đúng (lỗi gây tạm dừng 2026-11-15)
Test thực hiện: bộ lượt 1-3 chạy lại (7 hãng, `limit` 1..50, CATO áp đảo, 5 mức, ổn định, `more`/`count`, ký tự `% _ \ !`, cùng mã khác hãng, thẻ khách ≤ 10, thẻ nội bộ ≤ 50 mặc định 20, `limit` sai → 400, 120.000 sản phẩm). Thứ tự kết quả đổi so với `351cf1a` là có chủ ý của I7 (U1 so theo từng mã chứ không theo vị trí).
Kết quả: PASS
Evidence: 0 fail.
Negative case đã thử: xem trên.
Residual risk: `limit` dài hàng nghìn chữ số chưa thử trên Python ≥ 3.11 (hành vi có từ bản cũ).

## Claim I8 — Pháp chế và tồn kho không đổi nghĩa so với bản đã duyệt
Test thực hiện: bộ lượt 1-3 chạy lại (`NO_RULE` tường minh, ngoại lệ thủ công, 32 sản phẩm so nội bộ/khách/bản cũ, `pack_confirmed`/`same_cas` của thẻ khách như cũ, lược đồ tiền tệ cũ → `null` + `LEGACY_*`) + I1/I3 lượt 4. `pack_confirmed` và `same_cas` của thẻ khách giống bản cũ (đo ở U1: 45 lời gọi × 4 bố cục); mã đang hỏi nay đúng `IN_STOCK`/`quantity` theo oracle ở cả 5 mã `U1` (kể cả mã sắp sau hàng nghìn lô cùng CAS).
Kết quả: PASS
Evidence: 0 khác biệt ở mọi trường so sánh ngoài khác biệt có chủ ý (đọc đủ lô của mã hỏi).
Negative case đã thử: xem trên.
Residual risk: xem I1/I3.

## Hiệu năng truy vấn mới (ORDER BY trong mỗi nhánh)
Đo trên PostgreSQL 16 local, 300.000 lô / 60.000 mã / 6 hãng (+ CAS nóng 52.000 lô); `EXPLAIN (ANALYZE)` của đúng dạng truy vấn `_stock_lots`, trung vị 3 lần, không ORDER BY nhánh → có ORDER BY nhánh:

| Tình huống | Trước (ms) | Sau (ms) |
| --- | --- | --- |
| 1 mã + CAS 20 mã | 0,8 | 0,8 |
| 50 mã | 21,9 | 21,3 |
| CAS nóng 52.000 lô + mã hỏi 30 lô | 6,5 | 31,1 |
| `/stock?cas=` CAS nóng 52.000 lô (trần 50.000) | 5,5 | 30,7 |

HTTP (trung vị/tối đa, ms; thẻ khách | nội bộ): `/products` mã nóng 92/103 | 95/101; `/products` CAS nóng 92/98 | 99/102; `/stock?cas=` nóng 180/184 | 259/264; `/stock?code=` 10/14 | 8/9; `/products` theo tên 65/67 | 81/82. Bản cũ cùng lời gọi thẻ khách `/products` mã nóng: 91 ms; bản mới 91 ms. Tình huống cực đoan, MỘT CAS có 300.000 lô: `/products` 86/90 | 161/176; `/products` CAS 91/101 | 166/177; `/stock?cas=` 253/262 | 346/348 (bản cũ thẻ khách 76 ms cho mã hỏi). Tất cả << `statement_timeout` 3 giây. Kết luận: ORDER BY trong nhánh chỉ tốn thêm chục mili-giây khi một nhánh có hàng chục nghìn dòng (top-N sort), không đáng kể ở tải thường; không đổi nội dung thẻ nội bộ ngoài việc làm tập bị cắt tất định.

## Claim I9
Không kiểm (UAT n8n/Google Sheet do bên khác chạy). Lưu ý cho người chạy: (1) `lots` tối đa 200 lô mỗi sản phẩm/nhóm (có `lots_total`, `lots_truncated`); (2) số lượng đúng đến 50.000 lô của các mã trong kết quả, vượt thì cắt (nay tất định theo hãng/mã/quy cách/hạn) nhưng không có cờ riêng; (3) giá hỏng trả `unit_price_vnd=null` + `INVALID_PRICE_DATA`, giá rỗng/chữ trả `0` + `NO_PRICE_DATA` — workflow phải đọc `price_status`.

## Sửa test của verifier (nêu lý do, không hạ chuẩn)
- `test_machine_api_internal_round3.T2b.test_customer_same_cas_groups_match_old_when_late_rows`: nay so `same_cas`/`same_cas_more` và mọi phản hồi khác từng byte với bản cũ, chỉ bỏ qua khối `stock` (status/pack_confirmed/quantity) của `MID-OWN` — khác biệt có chủ ý đã phán xét ở I3 — và kèm kiểm bằng oracle SQL (tổng 6, bản mới phải `IN_STOCK`, đúng 1 lần). Các ca độc lập-thứ-tự-vật-lý không bị nới.
- `test_machine_api_internal_round3` (T2, T2b): thêm `cls.base_entries = list(env.entries)` sau `add_token("cqty", ...)`. Lỗi của chính test lượt 3: `setUp()` ghi lại `base_entries` (chụp trước khi thêm thẻ), nên thẻ `cqty` bị 401 ở cả hai bên và các so sánh của thẻ đó rỗng. Thêm kiểm "không được 401 cả hai bên". Sau khi sửa, T2 lộ một khác biệt có chủ ý nữa: `/stock?cas=` của thẻ `stock_detail=quantity` có `quantity` đúng oracle (18.020) thay vì 14.995 của bản cũ do cắt 5.000 — test nay bỏ riêng khóa `quantity` của ca này nhưng BẮT BUỘC bằng oracle và không nhỏ hơn bản cũ; mọi khóa khác giữ so từng byte.
- Không đổi bộ lượt 1-2.

## Task status

attempt: 3 / max_auto_repairs: 3 (đã dùng hết; lần 3 do PO duyệt thêm)
same_claim_repeat_fail: 0 (I1 FAIL lượt 3 → PASS lượt 4; I3 PASS từ lượt 3; không claim nào FAIL hai lượt liền)
next_action: DONE (I1..I8 đều PASS, 333/333 test đạt; việc còn lại là HUMAN_REVIEW/PO cho các mục SPEC_AMBIGUOUS/VERIFICATION_CONTRACT_PROBLEM đã nêu: "tối đa 200 lô", trần 50.000 lô không có cờ riêng, nhãn profile trong log; và I9 do bên khác chạy)
