# Phase 6D7 — gates rollout / UAT

Chưa được phép triển khai. Coordinator review và đóng gói, staging UAT, rồi xin
production approval riêng. Implementer không SSH/commit/push/PR/deploy.

Không migration mới hoặc thay dữ liệu. Giữ database schema032. Trước staging,
xác minh index007/008 upper(trim(code/cas)) và010 rawGIN name/code/CAS đúng table,
expression/opclass/predicate, valid và ready, không chỉ tên tồn tại. Metadata do
user cung cấp đã xác nhận production có đủ index; cần kiểm tra staging riêng.
Không tự chạy010 IF NOT EXISTS để che index sai/invalid, không tự drop/rebuild.
Nếu thiếu/sai: dừng, coordinator review phương án có maintenance/rollback gate.

Giữ stats phù hợp, theo dõi latency/degraded và connections ở staging. ANALYZE,
index DDL hay cấu hình server cần gate riêng; không suy quyền từ benchmark local.
Gợi ý không gọi /search khi gõ; query500ms tổng stage,150ms/statement,100ms/lock,
connect1s. Auth/middleware/network không nằm trong budget SQL; đây không phải
hard deadline toàn HTTP. Mỗi query trả<=10, không cache cross-user. Khi bận,
gợi ý có thể không đầy đủ nhưng người dùng vẫn bấm Search như trước.

Main /search giữ semantics catalog cũ (kể cả wildcard), chỉ nhánh tên tồn mới
escape wildcard và giới hạn direct stock1.000 với cảnh báo refine. Suggestion
value là field hiển thị được, chọn sẽ chạy search explicit. Exactname ưu tiên
trong bounded prefix candidates, không hứa ranking fuzzy/toàn bộ/bỏ dấu.

## UAT cần xác nhận

1. Tìm tên tồn thật (ví dụ tên của HI70024P), code và CAS; lịch sử không xuất hiện.
   Tên catalog+tên tồn cùng khớp vẫn đủ stock-only; không trùng stock đã attach.
2. Gõ3ký tự ->gợi ý sau~300ms; kiểm tra exact/prefix/contains, chữ Việt, %, _, !,
   không kết quả, chuỗi dài. Giá/compliance không có trong gợi ý.
3. Admin/team giới hạnbrand/teamkhôngSEARCH; VIEW_NAME/CODE/CAS thiếu từng trường,
   CAS chỉ được suggest khi cảSEARCH_BY_CAS+VIEW_CAS. Không dùng label/value để
   lộ fieldẩn. Đổi quyền rồi request mới không giữ cache cũ.
4. ArrowUp/Down, Enter đúng1search, Escape, Tab/blur, clickoutside, click/touch
   option; bộ gõ Việt khi Enter đang composition không search; gõ nhanh/xóa khi
   requestchậm không listcũ hiện lại. Desktop/mobile không list tràn màn hình.
5. Đủ quantity/expiry/note/stockprice; giá tồn không thành giá báo giá; stock-only
   không được chọn xuất. Search/FindCode/CheckLicense/QuickQuote/copy/export như cũ.
6. Theo dõi common/rare/missing với dataset staging đại diện. Local1M rows nhỏ
   hơn production và statsfresh; không dựa riêng số ms local để duyệt SLO.

Rollback: revert application về cd7ebb7 và asset version tương ứng; không DDL,
không sửa/xóa stock snapshot/ledger. Phần thêm/sửa nhanh6D6 vẫn nguyên trên baseline.

Đóng gói source/templates/static/tests/benchmark script và docs gọn. PNG/log/JSON
EXPLAIN là evidence local, không đưa vào release. Ba test real quote template cần
QUOTE_TEMPLATE_FIXTURE riêng nếu muốn hoàn thành ngoài bộ test bundled.
