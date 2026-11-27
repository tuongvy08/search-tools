# Tra hàng loạt qua cổng API máy — `POST /machine/v1/products/batch`

Trạng thái: PO duyệt phương án 2026-11-26 ("Đồng ý"), kể cả sửa Nginx cho riêng đường này. Mức **HIGH** (giá qua Internet). **Production đã chạy từ 2026-11-27** (commit `ba84159`, PR #43; chạy thật qua n8n đạt). Hợp đồng kiểm chứng: [specs/machine-api-batch/VERIFICATION.md](../../specs/machine-api-batch/VERIFICATION.md). Vận hành: [OPERATIONS mục 8](OPERATIONS.md).

## Nhu cầu

n8n cần tra nhiều mã/CAS một lần: bù giá mua cho ~5.200 mã (sau đó vài chục dòng/ngày), lập báo giá hàng trăm dòng, tra CAS hàng loạt. Trước đây chỉ có `GET /products?q=` (một từ khóa, tìm kiểu "chứa"); 120 mã mất ~135 giây và Nginx trả 429 khi gọi dày.

## Cách gọi

```http
POST /machine/v1/products/batch
Authorization: Bearer <thẻ>
Content-Type: application/json

{"items": [{"code": "T0050000", "ref": "dong-12"}, {"cas": "64-17-5", "ref": "dong-13"}],
 "include": ["compliance", "stock"], "max_per_item": 20}
```

| Trường | Ý nghĩa |
| --- | --- |
| `items` | 1–500 mục. Mỗi mục có **đúng một** `code` hoặc `cas` (chuỗi ≤ 100 ký tự), `ref` tùy chọn (chuỗi ≤ 64 ký tự, trả lại nguyên văn để n8n ghép dòng). |
| `include` | Thêm `compliance` (pháp chế) và/hoặc `stock` (tồn kho). Mặc định không có — nhanh hơn. Thẻ nội bộ luôn có khối `price`; thẻ khách không bao giờ có. |
| `max_per_item` | Số sản phẩm tối đa trả cho mỗi mục. Thẻ nội bộ: mặc định 20, trần 50. Thẻ khách: 10. |

Khớp **chính xác**: bỏ khoảng trắng đầu/cuối, không phân biệt hoa/thường (`UPPER(TRIM(...))`, dùng chỉ mục `idx_products_code_upper_trim` / `idx_products_cas_upper_trim`). Không trả sản phẩm chỉ "chứa" giá trị. Khoảng trắng ở **giữa** giá trị phải đúng như đã lưu.

## Kết quả

```json
{"count": 2, "max_per_item": 20, "include": ["compliance", "stock"],
 "summary": {"found": 1, "not_found": 0, "invalid": 0, "not_allowed": 0, "truncated": 1},
 "items": [
   {"position": 1, "ref": "dong-12", "type": "code", "value": "T0050000", "status": "FOUND",
    "matches_total": 1, "truncated": false, "results": [ {"name": "...", "code": "...", "price": {...}, "compliance": {...}, "stock": {...}} ]},
   ...],
 "disclaimer": "..."}
```

- `items` luôn **đủ và đúng thứ tự** gửi lên (`position` bắt đầu từ 1). Mục trùng nhau được trả lặp lại.
- `status`: `FOUND`, `NOT_FOUND`, `INVALID` (mục sai — có `message`), `NOT_ALLOWED` (thẻ không được tra theo loại đó).
- `results`: **mọi** sản phẩm khớp (tới `max_per_item`); hệ thống không tự chọn một kết quả. Mỗi phần tử y hệt phần tử của `/products` cho cùng sản phẩm (cùng hàm dựng `_result_item`).
- `matches_total` = tổng số sản phẩm khớp mà thẻ được thấy; `truncated=true` khi bị cắt — khi đó các hãng được xen đều.
- Một lời gọi tính **một** lượt trong giới hạn tần suất của thẻ.
- Mục `INVALID` không trả lại `value` (có thể chứa ký tự không mã hóa được); ghép dòng bằng `position`/`ref`. Ký tự điều khiển (C0, C1) và nửa cặp UTF-16 lẻ trong `code`/`cas`/`ref` → mục đó `INVALID`, các mục khác không bị ảnh hưởng.

## Giới hạn và lỗi

| Tình huống | Phản hồi |
| --- | --- |
| > 500 mục | 400 `too_many_items` — chia nhỏ |
| Tổng sản phẩm trả về > 10.000 | 400 `too_many_results` — giảm số mục hoặc `max_per_item` |
| Thân > 128 KB | 413 |
| JSON sai, khóa lạ ở cấp ngoài, `include`/`max_per_item` sai | 400 |
| Thẻ sai/hết hạn 401; IP lạ / thiếu quyền 403; gọi dồn 429; DB bận/quá 8 giây 503 `service_busy` | như các đường khác |

## Thiết kế (AI quyết)

- Một truy vấn cho các mục `code`, một cho các mục `cas`: `unnest(...) WITH ORDINALITY` + `CROSS JOIN LATERAL` để mỗi giá trị dùng chỉ mục biểu thức; trong lateral đếm `matches_total` và đánh `brand_rank` (xen hãng) rồi `LIMIT max_per_item`. Giá trị trùng chỉ tra một lần.
- Lọc hãng của thẻ khách đặt SAU bước tra chỉ mục (`OFFSET 0`), vì để chung điều kiện thì planner gộp chỉ mục hãng và chậm (đo staging 11 giây → 1,6 giây).
- Gợi ý `same_cas`: mỗi sản phẩm nhận đúng cửa sổ lô mà `/products` thấy khi tra riêng mã đó (5.000 lô đầu tiên của các mã khác cùng CAS), không phụ thuộc mục khác trong gói.
- Pháp chế qua đúng `product_resolver_lateral` của `/products`; tồn kho qua `_stock_lots` (một truy vấn cho mọi mã), gom theo mã/CAS bằng dict để không quét lặp. Lô cùng CAS đọc riêng từng CAS (`_same_cas_lot_lists`, xem dòng trên).
- `statement_timeout` 8 giây **chỉ trong giao dịch của lời gọi batch** (`SET LOCAL`); các đường khác giữ 3 giây. Gunicorn/Nginx timeout 15 giây.
- Quyền: như `/products` (thẻ khách lọc hãng theo team, quyền trường; mục `code` cần SEARCH+VIEW_CODE; mục `cas` cần SEARCH_BY_CAS+VIEW_CAS). Bộ lọc cuối `_assert_clean` áp dụng cho toàn phản hồi.
- Nhật ký: một dòng như các đường khác (`endpoint=products_batch`, số sản phẩm trả về); không ghi mã/CAS/`ref`/giá/thẻ. Nginx không ghi thân yêu cầu.
- Nginx: khối con `location = /machine/v1/products/batch` với `client_max_body_size 128k` (các đường khác giữ 8k), xem [mẫu](../../deploy/nginx-machine-api.conf.example). Không đổi giới hạn tần suất.

## Đo trên staging (chỉ đọc, 2026-11-26, trước khi code)

1.262.173 sản phẩm. Mỗi mã khớp tối đa 5 sản phẩm (trung bình 1,06); mỗi CAS tối đa 3.419 (p99 = 23, trung bình 2,68). Truy vấn mẫu: 500 mã ngẫu nhiên ≈ 0,1 s; 500 CAS ngẫu nhiên ≈ 0,27 s; 500 CAS phổ biến nhất (trần 51/mục) ≈ 3,5 s gồm cả bước chọn mẫu quét toàn bảng. Kết quả đo sau triển khai: [OPERATIONS mục 8](OPERATIONS.md).

## Hoàn tác

Thu hồi thẻ hoặc khôi phục `machine_api.py` bản trước + `systemctl restart search-tools-machine-api`; gỡ khối `location = /machine/v1/products/batch` khỏi snippet Nginx → `nginx -t` → `reload`. Không có thay đổi dữ liệu/lược đồ.
