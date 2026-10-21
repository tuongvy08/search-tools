# Verification Contract — api-mcp-integration (cổng máy chỉ đọc cho n8n/AI) — v1 (DỰ THẢO, chưa khóa; đã sửa theo phản biện Codex 2026-10-21)

Khóa khi PO duyệt kế hoạch [PLAN](PAUSED_quote_gateway_PLAN.md). Sau khi implement bắt đầu, file này là hợp đồng đã khóa — xem "Thay đổi hợp đồng" ở cuối file. Giả định phương án A (nháp ở Google Sheet, ứng dụng không ghi dữ liệu); nếu PO chọn B, viết lại v2 trước khi làm.

## Claim V1 — Không có thẻ hợp lệ thì không đọc được gì

Mọi điểm vào `/machine/v1/*` (trừ `health`, chỉ trả tình trạng, không dữ liệu) từ chối khi thiếu thẻ, sai thẻ, thẻ hết hạn hoặc thẻ đã bị thu hồi.

Oracle: HTTP 401; body không chứa dữ liệu sản phẩm/giá/tồn kho; không có thông điệp phân biệt "sai thẻ" với "thẻ không tồn tại".

Test type: integration.

Failure case cần thử: thẻ đúng nhưng thêm/bớt 1 ký tự; thẻ rỗng; header `Authorization` sai dạng (`Basic`, `bearer` thiếu giá trị); thẻ hết hạn đúng thời điểm; thẻ nằm trong query string thay vì header (phải bị từ chối); thẻ của team đã bị xóa.

## Claim V2 — Thẻ chỉ dùng được từ IP đã khai báo

Thẻ đúng nhưng IP gọi không thuộc danh sách của thẻ thì bị từ chối. IP lấy từ địa chỉ kết nối đã qua `ProxyFix` đúng 1 hop; không có đường vào thẳng tiến trình ứng dụng từ bên ngoài.

Oracle: HTTP 403 cho IP ngoài danh sách dù thẻ đúng; HTTP 200 cho IP trong danh sách; cấu hình thẻ không có danh sách IP thì ứng dụng **từ chối nạp thẻ đó** (không mặc định "mọi IP"). Trên **staging, qua Nginx thật**: gửi `X-Forwarded-For` giả từ IP không được phép vẫn bị 403; kết nối thẳng tới cổng Gunicorn từ máy ngoài thất bại (chỉ lắng nghe loopback).

Test type: integration (logic) + kiểm tra trên staging (hạ tầng). Test trong Flask đơn thuần **không** đủ để PASS claim này.

Failure case cần thử: client tự gửi `X-Forwarded-For`/`X-Real-IP` mang IP được phép (nhiều giá trị, có dấu phẩy); danh sách IP rỗng; CIDR sai định dạng; IPv6/IPv4-mapped.

## Claim V3 — Thẻ máy chỉ mở được cổng máy, không mở được cổng nhân viên hay quản trị

Thẻ máy không đổi được phản hồi của bất kỳ route nào ngoài `/machine/v1/*`; việc miễn đăng nhập/IP văn phòng chỉ áp dụng đúng tiền tố đó.

Oracle: quét tự động mọi route của `app.url_map`; với mỗi route, phản hồi (mã + loại nội dung) khi gửi **kèm thẻ hợp lệ từ IP hợp lệ** **giống hệt** phản hồi của request ẩn danh không thẻ. Các route công khai có chủ đích (`static`, `login`, Google đăng nhập/callback) giữ đúng hành vi hiện có, được liệt kê rõ trong test; route chưa được phân loại làm test fail.

Test type: integration.

Failure case cần thử: đường dẫn lắt léo (`/machine/v1/../admin`, `//machine/v1/`, `/Machine/V1/`, `/machine/v1%2f..`), dấu `/` cuối, method khác (PUT/DELETE/OPTIONS/HEAD), route mới thêm sau này.

## Claim V4 — Máy không bao giờ thay đổi dữ liệu

Cổng máy chỉ đọc: không có điểm vào nào ghi vào database, file hay dịch vụ ngoài. Ngoại lệ duy nhất, nêu rõ: dòng log dịch vụ theo V7.

Oracle: (a) kết nối database của cổng máy ở chế độ **chỉ-đọc bắt buộc** — thử ghi qua kết nối đó phải báo lỗi; (b) so checksum/đếm dòng của **mọi bảng** và bộ đếm ghi của PostgreSQL (`pg_stat_user_tables`: insert/update/delete) trước và sau bộ ≥ 50 lời gọi (hợp lệ, sai, quá lớn, ngắt giữa chừng) → không đổi; (c) mọi phương thức ngoài `GET`/`POST` đã khai báo bị 405; (d) không file nào được tạo ngoài thư mục tạm.

Test type: integration (database test).

Failure case cần thử: payload cố nhồi lệnh ghi hoặc trường lạ (`"update":...`, `"team_id":...`); lời gọi song song; cookie phiên hết hạn/sai gửi kèm (không được kích hoạt ghi nhật ký đăng nhập hiện có trong `session_security`).

## Claim V5 — Máy thấy đúng bằng phần team của thẻ được thấy, không hơn

Kết quả cho thẻ gắn team T giống hệt kết quả của một nhân viên team T (không phải admin) gọi `/api/quote-assistant/match` với cùng dữ liệu vào: trạng thái khớp, sản phẩm, ứng viên, quyền xem trường, chính sách thương hiệu.

Oracle: bộ dữ liệu chuẩn (golden) ≥ 25 dòng, đủ trạng thái; so từng trường giữa phản hồi nhân viên và phản hồi máy — khác biệt duy nhất cho phép là các trường định danh phiên và các trường cổng máy chủ động lược bỏ (liệt kê rõ trong test).

Test type: integration (so sánh đối chiếu).

Failure case cần thử: team **thiếu `QUICK_QUOTE` hoặc `VIEW_PRICE`** → máy bị 403 y như nhân viên, và thẻ gắn team như vậy không được nạp; trường bị cấm theo quyền (tuân thủ, hàng hóa, giá) **không xuất hiện ở bất kỳ chỗ nào**, kể cả trong `candidates`/`selected_candidates` và tên phụ; team hạn chế thương hiệu → thương hiệu bị chặn không xuất hiện; team không có `SEARCH_BY_CAS` → dòng CAS bị từ chối như nhân viên; đổi quyền team rồi gọi lại có hiệu lực ở lần gọi kế tiếp; **giá nhập/giá vốn không bao giờ có trong phản hồi máy dù team có quyền**; team bị lưu trữ/xóa sau khi thẻ đã nạp → lần gọi kế tiếp bị từ chối; lỗi đọc database khi kiểm tra quyền → 503, không cho qua.

## Claim V5b — Tồn kho (chỉ nếu S0 xác nhận đưa vào bản đầu)

Nếu có trong phản hồi, tồn kho khớp với số liệu tồn kho thật mà team của thẻ được xem; nếu S0 quyết định không đưa vào bản đầu thì claim này được rút khỏi phạm vi bằng Change request có phê duyệt PO.

Oracle: với ≥ 10 mã có tồn kho đã biết (chuẩn bị bằng truy vấn độc lập vào database thử, không dùng chính hàm của ứng dụng), số tồn trong phản hồi máy bằng số kỳ vọng; mã không có tồn kho → trạng thái "không có", không để trống mơ hồ.

Test type: integration.

Failure case cần thử: hai kho (Hà Nội, Hồ Chí Minh); mã tồn bằng 0; mã chỉ có trong tồn kho nhưng không trong danh mục.

## Claim V5c — Cổng máy không bao giờ biến thẻ thành đăng nhập nhân viên

Phản hồi cổng máy không chứa `Set-Cookie`; không tạo hay sửa phiên đăng nhập; không thừa hưởng quyền admin.

Oracle: mọi phản hồi (thành công và lỗi) không có header `Set-Cookie`; lấy mọi cookie/header từ phản hồi máy (nếu có) rồi gọi cổng nhân viên **không kèm thẻ** → vẫn bị coi là ẩn danh; sau lời gọi máy, trạng thái toàn cục (`g`, `session`) của request kế tiếp trong cùng tiến trình sạch (không rò quyền giữa các request).

Test type: integration.

Failure case cần thử: lỗi giữa chừng khi đang chạy với quyền máy (ngữ cảnh phải được phục hồi); hai lời gọi liên tiếp, thẻ khác team; request nhân viên ngay sau request máy trong cùng worker.

## Claim V6 — Đầu vào xấu hoặc quá lớn bị chặn gọn gàng, dịch vụ nhân viên không bị ảnh hưởng

Oracle: body > 256 KiB → 413 **trước khi** đọc JSON; > 200 dòng → 413; JSON hỏng/kiểu sai → 400 thông điệp chung; vượt tần suất hoặc đồng thời → 429; truy vấn quá thời gian cho phép → bị hủy, trả lỗi chung; không lần nào lộ SQL, đường dẫn file, traceback, giá trị cấu hình. Trong khi một thẻ bị giả lập "tấn công" ở mức giới hạn, một request nhân viên chạy song song vẫn trả lời trong ngưỡng thời gian được ghi trong test (đo trên staging).

Test type: integration + đo trên staging.

Failure case cần thử: ít dòng nhưng chuỗi cực dài; mã làm khớp ra rất nhiều ứng viên; dòng rỗng toàn bộ; ký tự SQL; unicode lạ; trường trùng khóa JSON; body không phải JSON; `Content-Length` giả; nhiều worker cùng chịu tải.

## Claim V7 — Mọi lần gọi đều truy vết được, không lộ bí mật

Oracle: mỗi lời gọi (thành công hay bị từ chối) sinh đúng 1 dòng nhật ký có `service:<tên>` (hoặc `anonymous` nếu thẻ không xác định được), điểm vào, IP, số dòng, mã kết quả; quét toàn bộ nhật ký phát sinh trong test **không chứa** chuỗi thẻ, mã băm thẻ, giá, hay nội dung dòng yêu cầu.

Test type: integration.

Failure case cần thử: thẻ sai (không được ghi lại thẻ sai vào log), lỗi nội bộ giữa chừng (stack trace không chứa dữ liệu), thẻ rất dài.

## Claim V8 — Cổng nhân viên hiện có không bị hồi quy

Oracle: toàn bộ bộ test hiện có pass (số lượng và tên không giảm); thêm test xác nhận: ngoài tiền tố `/machine/v1/`, người dùng ẩn danh từ IP ngoài văn phòng vẫn bị chặn như cũ; session và đăng nhập Google hoạt động như cũ; hành vi `session_security`, `middleware_access`, `team_permissions` không đổi.

Test type: regression (suite hiện có) + integration.

Failure case cần thử: thứ tự `before_request` (đăng ký cổng máy không được làm `session_security`/IP middleware bị bỏ qua cho route khác), thẻ hợp lệ gửi kèm cookie phiên của nhân viên khác (không được thừa hưởng quyền của phiên đó); cookie phiên hết hạn gửi vào cổng máy (không được kích hoạt ghi nhật ký đăng nhập).

## Claim V9 — Tắt được thẻ nhanh và an toàn

Oracle: sau khi xóa thẻ khỏi cấu hình và khởi động lại, mọi lời gọi dùng thẻ đó trả 401; cấu hình thẻ sai (thiếu team, thiếu IP, hạn dùng không hợp lệ, team không tồn tại) làm ứng dụng ghi cảnh báo và **không** kích hoạt thẻ đó, các thẻ còn lại và cổng nhân viên vẫn chạy bình thường.

Test type: integration.

Failure case cần thử: biến cấu hình rỗng; trùng mã băm giữa hai thẻ; thẻ hết hạn trong lúc đang chạy.

## Claim V10 — Luồng Google Sheets → n8n → nháp đúng và không bịa (UAT trên staging, Sheet dữ liệu giả)

Oracle: với Sheet thử 25 dòng gồm: dòng mã hợp lệ khớp một sản phẩm, dòng CAS, dòng mã sai, dòng chỉ có tên, dòng trùng, dòng trống, dòng **số lượng âm/chữ/trống**, dòng ở trạng thái **cần xem xét (`REVIEW`)**, dòng bị chặn (thương hiệu/quyền), dòng thiếu giá hợp lệ, dòng có **nhiều ứng viên** → tab nháp có đủ **25 dòng ra**; chỉ dòng có một sản phẩm đã chọn rõ ràng mới được điền mã/tồn kho/giá bán (khớp tra tay trên ứng dụng); mọi dòng còn lại mang "cần người xử lý" và **để trống** mã/giá, không lấy ứng viên đầu tiên; số lượng ở nháp bằng đúng số lượng nhập (không đổi khi chia lô hay chạy lại); chạy lại cùng Sheet không tạo dòng nháp trùng; không dòng nào bị mất thầm lặng.

Test type: manual UAT (PO cùng quan sát) + kiểm tra bản ghi thực thi của n8n.

Failure case cần thử: n8n gọi lỗi giữa chừng (tắt dịch vụ staging) → dòng được đánh dấu lỗi và chạy lại được; Sheet 450 dòng (vượt 200) → chia lô, không mất hay trùng dòng; thẻ hết hạn giữa chừng → dừng rõ ràng, không điền dở.

## Claim V11 — MCP chỉ đọc và trung thực

Oracle: danh sách công cụ MCP chỉ gồm công cụ đọc; không công cụ nào gọi endpoint khác `/machine/v1/*`; khi API lỗi/401/403/413/429, MCP trả lỗi rõ ràng cho AI thay vì giá trị tự suy ra; MCP không lưu thẻ trong log.

Test type: integration + UAT.

Failure case cần thử: thẻ hết hạn giữa phiên, API không trả lời, AI yêu cầu "cập nhật giá" (phải không có công cụ nào để làm việc đó).

## Ngoài phạm vi

Máy ghi trực tiếp vào dữ liệu thật (bước 3); bảng nháp trong database; giao diện quản lý thẻ; tìm sản phẩm theo tên; kiểm thử tải lớn; mọi thao tác production (do release riêng, cần PO "đồng ý bắt đầu"); bảo mật phía Google/n8n ngoài ứng dụng.

---

## Thay đổi hợp đồng

Không tự sửa file này sau khi implement đã bắt đầu để làm test dễ pass hơn. Cần thay đổi claim hoặc oracle:

1. Ghi lý do thay đổi vào "Change request log" dưới đây.
2. Xin phê duyệt — người dùng cho thay đổi yêu cầu; verifier độc lập đủ nếu chỉ làm rõ oracle, không đổi yêu cầu.
3. Tăng version, ví dụ đổi tiêu đề thành "VERIFICATION.md v2".
4. Chạy lại toàn bộ verification từ đầu với version mới — không tính là repair của version cũ.

### Change request log

<để trống nếu chưa có thay đổi>
