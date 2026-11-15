# Cổng API cho bot/AI (chỉ đọc) — Kế hoạch thiết kế

Phạm vi: [SCOPE](SCOPE.md). Hợp đồng kiểm chứng (khóa trước khi code): [specs/machine-api-readonly/VERIFICATION.md](../../specs/machine-api-readonly/VERIFICATION.md). Nền thiết kế cũ đã qua phản biện Codex 11 điểm: [archive](../archive/api-mcp-integration/PAUSED_quote_gateway_PLAN.md).

## 1. Khác với thiết kế cũ (và vì sao)

| Thiết kế cũ (2026-10-21) | Bản này | Lý do |
| --- | --- | --- |
| Thêm route `/machine/v1/*` vào chính ứng dụng nhân viên | **Dịch vụ riêng** `machine_api.py` (Gunicorn + cổng loopback riêng, Nginx chuyển `/machine/` tới đó) | Ứng dụng nhân viên có 4 lớp chặn/ghi nhật ký gắn vào phiên đăng nhập; chen route máy vào dễ mở nhầm cửa hoặc kích hoạt ghi nhật ký đăng nhập. Tách riêng: **không sửa một dòng nào** của `search.py`, `session_security.py`, `middleware_access.py`, `team_permissions.py`; bot đông cũng không làm chậm nhân viên. |
| Bắt buộc IP khai báo | IP **tùy chọn** theo từng thẻ (nên dùng khi nền tảng bot có IP cố định) | Nhiều nền tảng chatbot không có IP cố định; lớp bảo vệ chính là thẻ + giới hạn tần suất + dữ liệu đã bị lọc cứng. |
| Có giá bán theo team | **Không bao giờ có giá** | PO quyết (khách hỏi). |
| Thu hồi thẻ = sửa env + restart | File thẻ **đọc lại tự động** (≤ 5 giây) | Thu hồi tức thì khi nghi lộ thẻ. |

## 2. Thành phần

- `machine_api.py` — Flask app độc lập, **chỉ GET**, không session/cookie.
- File thẻ `MACHINE_API_TOKENS_FILE` (JSON, **ngoài Git**, quyền `0600`): mỗi thẻ gồm `id`, `sha256` của thẻ (thẻ thật chỉ người giữ biết), `team_id`, `expires_at`, `enabled`, `stock_detail` (`status`|`quantity`), `rate_per_minute`, `allowed_ips` (tùy chọn).
- `scripts/machine_token.py` — sinh thẻ mới (in thẻ **một lần**) + dòng cấu hình băm.
- Kết nối database: `MACHINE_DATABASE_URL` nếu có (khuyến nghị dùng tài khoản DB chỉ SELECT, **theo cột** — không đọc được cột giá/ghi chú nội bộ — mẫu ở `sql/machine_readonly_role.sql`, KHÔNG phải migration), nếu không dùng `DATABASE_URL`; luôn `readonly`, `statement_timeout=3s`.
- `integrations/mcp/` — MCP server nhỏ cho Claude/AI agent, chạy ở máy dùng AI (không đặt trên server production), chỉ gọi cổng.
- `deploy/` — unit systemd + mẫu Nginx cho dịch vụ.
- `docs/machine-api/HUONG_DAN_KET_NOI.md` — hướng dẫn n8n/chatbot/Claude bằng tiếng Việt đời thường; `openapi.json` do cổng tự phục vụ.

## 3. Điểm vào (tất cả `GET`, JSON)

| Điểm vào | Việc | Điều kiện |
| --- | --- | --- |
| `/machine/v1/health` | Báo sống (không dữ liệu, không cần thẻ) | — |
| `/machine/v1/products?q=…&limit=…` | Tìm theo tên/mã (và CAS nếu team có `SEARCH_BY_CAS`); mỗi kết quả kèm pháp chế + tồn kho | `SEARCH`; q ≥ 3 ký tự, ≤ 100; limit ≤ 10 |
| `/machine/v1/compliance?cas=…` | Tình trạng pháp chế theo CAS | `CHECK_LICENSE` (kéo theo `SEARCH_BY_CAS`, `VIEW_COMPLIANCE`) |
| `/machine/v1/stock?code=…` hoặc `?cas=…` | Còn hàng/hết hàng theo mã hoặc CAS (cùng CAS liệt kê riêng) | `SEARCH` (+`SEARCH_BY_CAS` nếu tra theo CAS) |
| `/machine/v1/openapi.json` | Mô tả API để n8n/GPT Actions nhập | — |

Lọc cứng ở cổng, **không phụ thuộc quyền team**: không có `price`, `ship`, `note` nội bộ, `stock_price`, `stock_note`, `product_id`, `Stock_Item_Id`. Trường còn lại vẫn qua quyền `VIEW_*` của team (tên/mã/CAS/hãng/quy cách/pháp chế/ghi chú pháp chế).

## 4. Trạng thái pháp chế trả về

`compliance.state`: `RULE` (có nhãn; kèm `label`, `export_policy` ALLOW|BLOCK, `note`, `source`), `NO_RULE` (hệ thống **chưa có quy tắc**; kèm câu cố định "Chưa có quy tắc trong hệ thống — cần xác nhận với pháp chế; không có nghĩa là được phép nhập"), `DATA_ERROR` (ngoại lệ thủ công hỏng → coi là chặn), `HIDDEN` (team không có quyền xem pháp chế). Luôn kèm `disclaimer` cố định: dữ liệu tham khảo, chưa có căn cứ pháp lý/số nghị định trong hệ thống.

## 5. Tồn kho

Theo mã (`exact_code`): `status` = `IN_STOCK` / `OUT_OF_STOCK`; `expiry_warning=true` khi **mọi** lô còn dùng được đều sắp hết hạn (≤ 30 ngày; còn một lô hạn dài thì không cảnh báo); hàng chỉ còn lô hết hạn → `OUT_OF_STOCK`. Cùng CAS khác hãng/quy cách: `same_cas` tối đa 5 mục (hãng, quy cách, trạng thái) — **không** gộp vào trạng thái của mã gốc để khỏi nói "có hàng" sai. Số lượng chỉ có khi thẻ bật `stock_detail=quantity`.

## 6. An toàn

Thẻ ngẫu nhiên 256-bit, chỉ lưu SHA-256; so sánh hằng-thời-gian; thẻ chỉ qua header `Authorization: Bearer` (không nhận qua URL); 401 chung cho mọi lỗi thẻ; giới hạn tần suất theo thẻ (trong bộ nhớ tiến trình; dịch vụ chạy **1 tiến trình × 8 luồng** nên giới hạn chính xác) + `limit_req` ở Nginx; nhật ký một dòng/lần gọi, chỉ có id thẻ, điểm vào, mã trả về, số kết quả, IP, thời gian (**không** chứa thẻ, từ khóa, giá); quyền/team đọc **mỗi lần gọi** (team bị lưu trữ → từ chối; lỗi DB → 503, không cho qua); lỗi chung không lộ SQL/traceback. Rủi ro còn lại phải nói thẳng: thẻ bị lộ cho phép kẻ xấu **cào** dữ liệu hàng hóa/tồn kho trong giới hạn tần suất → thu hồi bằng sửa file thẻ.

## 7. Các bước và cổng duyệt

| Bước | Nội dung | Môi trường | Cổng |
| --- | --- | --- | --- |
| 1 | Hợp đồng kiểm chứng khóa; test trước | local | — |
| 2 | `machine_api.py` + công cụ sinh thẻ + test | local (PostgreSQL Docker) | developer test |
| 3 | MCP + OpenAPI + hướng dẫn | local | test MCP |
| 4 | Verifier độc lập (Claude, ngữ cảnh sạch) + review Codex chỉ-đọc | local | PASS không finding chặn |
| 5 | Triển khai và thử **thật qua Nginx** trên staging (kiểm V11) | staging (tự do theo PO 2026-10-02) | smoke + UAT |
| 6 | Lên production | production | **PO nói "đồng ý bắt đầu"** |

## 8. Hoàn tác

Ứng dụng chỉ đọc nên không có dữ liệu cần hoàn tác. Tắt tức thì: `enabled:false`/xóa thẻ trong file (≤ 5 giây) hoặc `systemctl stop search-tools-machine-api`. Ứng dụng nhân viên không bị chạm tới ở bất kỳ bước nào.
