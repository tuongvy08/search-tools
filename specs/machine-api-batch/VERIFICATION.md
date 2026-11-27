# Verification Contract — machine-api-batch — v2 (v1 khóa 2026-11-26; v2 làm rõ B4 theo PO 2026-11-27, xem Change request log)

PO duyệt phương án 2026-11-26 ("Đồng ý"): tra nhiều mã/CAS trong một lần gọi; sửa Nginx cho riêng đường batch; dùng lại thẻ nội bộ có sẵn. Mức **HIGH** (mở giá qua Internet). Nền: mọi claim của [machine-api-readonly](../machine-api-readonly/VERIFICATION.md) và [machine-api-internal-price](../machine-api-internal-price/VERIFICATION.md) phải còn đúng (B1). Kế hoạch/thiết kế: [docs/machine-api/BATCH.md](../../docs/machine-api/BATCH.md).

## Giao diện được khóa

`POST /machine/v1/products/batch`, `Content-Type: application/json`, thân:

```json
{"items": [{"code": "T0050000", "ref": "dong-12"}, {"cas": "64-17-5"}],
 "include": ["compliance", "stock"], "max_per_item": 20}
```

- `items`: 1..500 mục; mỗi mục có **đúng một** trong `code`/`cas` (chuỗi), `ref` tùy chọn (chuỗi ≤ 64 ký tự, trả lại nguyên văn).
- `include`: tập con của `compliance`, `stock` (mặc định rỗng). Thẻ nội bộ luôn có khối `price`; thẻ khách không bao giờ có.
- `max_per_item`: thẻ nội bộ mặc định 20, trần 50; thẻ khách mặc định 10, trần 10.
- Thân > 128 KB → 413. Khóa cấp ngoài lạ, `items` không phải danh sách/rỗng/> 500, `include` sai, `max_per_item` sai kiểu → 400 cả lời gọi.
- Tổng số sản phẩm trả về > 10.000 → 400 `too_many_results` (không cắt thầm lặng).

## Claim B1 — Không hồi quy

Oracle: toàn bộ test hiện có `tests/test_machine_api.py`, `tests/test_machine_api_internal.py` (và `tests/independent/*machine*` nếu có) PASS, số lượng không giảm; `/products`, `/compliance`, `/stock` trả đúng như trước (cùng nội dung JSON trên cùng dữ liệu); không sửa `search.py`/middleware ứng dụng nhân viên (diff so `origin/main`).

## Claim B2 — Đủ mục, đúng thứ tự, trạng thái tường minh

Oracle: với N mục gửi lên (có mục trùng nhau, khác hoa/thường, khoảng trắng đầu/cuối, mục sai), phản hồi có đúng N phần tử `items`, phần tử thứ k ứng với mục thứ k (`position` = k, `ref` khớp). Mỗi phần tử có `status` ∈ {`FOUND`, `NOT_FOUND`, `INVALID`, `NOT_ALLOWED`}, `matches_total`, `truncated`, `results`. Mục sai (thiếu/cả hai trường, không phải chuỗi, rỗng sau khi bỏ khoảng trắng, > 100 ký tự, ký tự điều khiển, khóa lạ) → `INVALID` kèm `message`, **không** làm hỏng các mục khác. `summary` đếm đúng.

Failure case: 500 mục đều trùng một mã; 500 mục đều sai; mục là số/null/danh sách.

## Claim B3 — Khớp CHÍNH XÁC, trả MỌI sản phẩm khớp, không tự chọn

Oracle: mục `code` khớp mọi sản phẩm có `UPPER(TRIM(code)) = UPPER(giá trị đã bỏ khoảng trắng đầu/cuối)` (tương tự `cas`); **không** trả sản phẩm chỉ "chứa" giá trị; mã trùng nhưng khác hãng/quy cách → trả **tất cả**. `matches_total` = tổng số sản phẩm khớp mà thẻ được thấy; `truncated = matches_total > len(results)`; khi bị cắt, các hãng được xen đều (≥ min(số hãng, max_per_item) hãng khác nhau trong kết quả). Thứ tự ổn định giữa các lần gọi. Ký tự `% _ !` trong giá trị là ký tự thường.

## Claim B4 — Trùng khớp với `/products` (giá, pháp chế, tồn kho)

Oracle: với mỗi sản phẩm trả bởi batch (≥ 30 sản phẩm, đủ dạng giá/tỷ giá/pháp chế/tồn kho, cả thẻ nội bộ và thẻ khách), phần tử tương ứng (cùng tên, mã, hãng, quy cách) trong `/products?q=<mã>` có **nội dung JSON y hệt** (khối `price`, `compliance`, `stock` gồm `lots`, `pack_confirmed`, `same_cas`). Thực hiện bằng một hàm dựng kết quả dùng chung (không chép logic).

(v2) Làm rõ, PO chốt 2026-11-27:
- **Không phụ thuộc mục khác:** kết quả của một mục (mọi khối, kể cả `stock.status`, `quantity`, `lots`, `lots_total`) giống hệt khi gửi riêng mục đó trong một gói 1 mục, dù cùng gói có mục nào khác.
- **Giá, pháp chế, `stock.status/quantity/pack_confirmed/lots/lots_total/lots_truncated`:** y hệt `/products?q=<mã>` cho cùng sản phẩm (trừ khi chính `/products` đã chạm trần 50.000 lô của trang).
- **Gợi ý `same_cas` / `same_cas_more`** được tính **riêng cho từng sản phẩm**: tập lô gợi ý của sản phẩm (mã C, CAS X) = tối đa 5.000 lô đầu tiên (thứ tự `brand_norm, code_norm, size_norm, expiry_date NULLS LAST`) trong các lô tồn kho đang hoạt động có CAS X, mã khác C, hãng thẻ được thấy; sau đó nhóm/lọc còn hàng/cắt 5 (khách) hoặc 10 (nội bộ) như `/products`. Kết quả này phải y hệt `/products?q=<mã>` khi trang `/products` không có sản phẩm nào khác làm thay đổi cửa sổ lô (ví dụ `limit=1`, hoặc tổng số lô cùng CAS của các mã khác trên trang ≤ 5.000). Khi chính `/products` cho `same_cas` khác nhau tùy `limit`/trang kết quả (trang có thêm mã "chứa" từ khóa, hoặc một mã có hai CAS, cùng > 5.000 lô), đối chiếu theo định nghĩa trên thay vì theo trang `/products`.

## Claim B5 — Quyền và bảo mật giữ nguyên

Oracle: thẻ khách không có khóa giá/ghi chú nào (`_assert_clean` áp dụng); chỉ thấy hãng của team; quyền trường (VIEW_NAME/CODE/CAS/BRAND/SIZE, VIEW_COMPLIANCE, VIEW_COMPLIANCE_NOTE) áp dụng như `/products`; mục `code` cần SEARCH+VIEW_CODE, mục `cas` cần SEARCH_BY_CAS+VIEW_CAS — thiếu quyền → mục `NOT_ALLOWED` (không lộ dữ liệu). Không có tham số nào nâng quyền (`profile`, `include_price`, header lạ bị bỏ qua hoặc 400). Không thẻ/sai thẻ 401, IP lạ 403, thẻ nội bộ thiếu DSN nội bộ 503, GET vào đường batch 405. Một lời gọi batch tính **một** lượt vào giới hạn tần suất của thẻ.

## Claim B6 — Nhật ký không rò bí mật

Oracle: dòng truy cập của batch chỉ có id thẻ, điểm vào `products_batch`, mã trạng thái, số kết quả, IP, ms — không có mã hàng/CAS/`ref`/giá/thẻ; lỗi 4xx/5xx không chứa SQL, DSN, giá trị gửi lên.

## Claim B7 — Nhanh, dùng chỉ mục, không ảnh hưởng ứng dụng nhân viên

Oracle (staging, dữ liệu thật ≈ 1,26 triệu sản phẩm): 500 mã ngẫu nhiên có thật, đầy đủ `include`, thẻ nội bộ: tổng thời gian phản hồi qua HTTPS ≤ 5 giây; kế hoạch truy vấn dùng `idx_products_code_upper_trim` / `idx_products_cas_upper_trim` (không Seq Scan trên `products`); 500 CAS phổ biến nhất không lỗi 5xx (hoặc 400 `too_many_results` nếu vượt trần); `/login` nhân viên vẫn 200 trong lúc chạy.

## Claim B8 — Nginx chỉ nới đúng đường batch

Oracle (staging): thân 100 KB tới `/machine/v1/products/batch` đi qua (không 413 từ Nginx); thân 100 KB tới `/machine/v1/products` vẫn 413; `nginx -t` đạt; `/login` nhân viên 200 trước và sau reload; nhật ký Nginx không chứa thân yêu cầu.

## Claim B9 — Chạy thật qua n8n (UAT staging)

Oracle: một lời gọi từ n8n (thẻ nội bộ staging) với ≥ 120 mã có thật + vài mã sai trả đủ dòng, đúng thứ tự; ≥ 10 dòng đối chiếu giá VND với truy vấn SQL độc lập khớp từng đồng.

## Ngoài phạm vi

Tìm "gần giống"/theo tên trong batch; ghi dữ liệu; thẻ mới; đổi giới hạn tần suất Nginx; production (cần PO "đồng ý bắt đầu").

---

## Thay đổi hợp đồng

Không tự sửa file này sau khi implement đã bắt đầu để làm test dễ pass hơn. Cần đổi: ghi lý do vào "Change request log" → xin phê duyệt → tăng version → chạy lại verification.

### Change request log

- 2026-11-27 (v2, PO duyệt "Đồng ý" sau khi verifier lượt 2 escalate: B4 FAIL hai lượt liên tiếp + SPEC_AMBIGUOUS): verifier chứng minh chính `/products` trả `same_cas` khác nhau cho cùng một sản phẩm tùy `limit`/trang kết quả, nên "y hệt `/products`" không có đáp án duy nhất ở ca > 5.000 lô cùng CAS. PO chốt: gợi ý cùng CAS tính riêng cho từng sản phẩm, giống tra riêng mã đó; mọi khối của một mục không được phụ thuộc mục khác trong gói (oracle B4 v2 ở trên). PO cho sửa lần 2 (lần cuối) trần lô theo mã dùng chung cả gói. Không đổi yêu cầu nào khác; chạy lại toàn bộ verification.
- 2026-11-26 (làm rõ oracle B1, không đổi yêu cầu): hai test độc lập cũ ghim đúng danh sách đường trong `openapi.json` (`test_machine_api_independent.V_Extra.test_openapi_is_public_and_clean_but_documents_no_price`, `test_machine_api_internal_round2...test_openapi_public_does_not_expose_internal_or_price_schema`) được cập nhật để chấp nhận thêm đúng `/products/batch` (POST, `tra_hang_loat`); mọi kiểm tra khác của hai test giữ nguyên (không có chữ price/ship/note trong tài liệu công khai, các đường cũ vẫn chỉ GET).
