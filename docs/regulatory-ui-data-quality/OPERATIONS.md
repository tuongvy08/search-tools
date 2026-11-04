# Chuẩn bị phát hành quy tắc CAS — 2026-11-02

Chưa commit, phát hành hoặc ghi production. Phạm vi PO đã duyệt: triển khai
và kiểm chứng local; truy cập giao diện production để xem dữ liệu liên quan.
Phần ghi production phải được duyệt riêng sau kiểm chứng độc lập.

## Hành vi của bản sửa

- Quy tắc tự động chỉ đối chiếu chính xác CAS. Không còn khớp tên/mã chung.
- Tình trạng gán trực tiếp cho sản phẩm vẫn ghi đè khi hãng bật ưu tiên.
- Quy tắc tên/mã cũ còn nguyên trong database/lịch sử, được ghi rõ không dùng
  đối chiếu; chỉ cho xem/ngừng. Không tự chuyển tên sang CAS.
- Không có quy tắc CAS thì không có cảnh báo tự động; đây không phải kết luận
  về việc được phép nhập khẩu. Bản sửa tác động mọi quy tắc tên/mã cũ, không
  chỉ một CAS. Cần PO chấp nhận tác động này trước phát hành.
- Hai nhãn sai bị loại khỏi lựa chọn thêm quy tắc, vẫn hiện ở danh mục để
  đối soát. Công cụ dọn là bước riêng, không chạy tự động khi deploy.

## Các bước trước production

1. Hoàn tất verifier độc lập theo `specs/regulatory-cas-only/VERIFICATION.md`.
2. PO duyệt checkpoint, commit/push/PR theo phạm vi cụ thể; CI/review áp dụng
   phải đạt. Triển khai staging bằng bản code đã khóa và thử quy tắc CAS,
   ưu tiên hãng, import CAS và menu; không dùng dữ liệu thật trong báo cáo AI.
3. Xin duyệt bước production cụ thể: nhận diện bản đang chạy, số quy tắc CAS
   và tên/mã theo trạng thái, kiểm hàng đợi import, sao lưu, kiểm hai nhãn
   bằng dry-run; không ghi dữ liệu trong bước kiểm tra.
4. Khi đủ evidence và PO đồng ý bắt đầu: dùng quy trình release bất biến
   hiện hành, đổi web và worker cùng phiên bản. Không cần migration.
   Không để worker cũ tiếp tục nhận file tên/mã khi web mới đã chạy.
   Preview cũ sẽ phải tạo lại vì fingerprint có phiên bản chính sách mới.
5. Kiểm sau phát hành: đăng nhập/menu, tìm sản phẩm CAS đã báo lỗi, CAS có
   quy tắc thật, sản phẩm có/không ưu tiên hãng, xuất báo giá theo quyền.
   Không sửa quy tắc hoặc sản phẩm thật chỉ để thử. Có lỗi thì dừng và báo PO;
   rollback release hoặc phục hồi dữ liệu cần duyệt theo SECURITY.md.

## Dọn nhãn sai — chỉ sau phê duyệt riêng

Trước khi cho chạy apply cần chốt thời gian chờ khóa và truy vấn hữu hạn
trong môi trường chạy đã duyệt (ví dụ 5 giây/30 giây), kiểm tra thiết lập
thực tế trên kết nối. CLI hiện chưa tự đặt timeout; chưa cho chạy apply
nếu điều kiện này chưa được chuẩn bị/kiểm chứng. Đồng thời kiểm tra nhãn
dạng text trên các hàng cũ có status_id NULL, ngoài các khóa ngoại do script
kiểm; có dữ liệu chưa rõ thì dừng dọn, lập phương án riêng.

Script không đọc `.env`; môi trường kết nối phải do quy trình vận hành đã
được duyệt cung cấp, không in chuỗi kết nối. Không mặc định dùng production.
Ví dụ lệnh (định danh cần được xác minh lại trong dry-run):

```sh
.venv/bin/python -B scripts/cleanup_invalid_regulatory_statuses.py --status 8=1563-66-2 --status 9=944-22-9
```

Mặc định là transaction chỉ đọc, snapshot nhất quán. Kết quả gồm định danh
database, bản trước, số tham chiếu theo mọi khóa ngoại, `can_delete` và
`fingerprint`. Chỉ trả về thông tin nhãn đang xét, không có sản phẩm/bảng giá.

Chỉ cho chạy `--apply --fingerprint <giá trị đã duyệt> --actor <người thực hiện>
--reason <lý do đã duyệt>` khi PO duyệt kết quả dry-run và xóa đúng nhãn.
Có tham chiếu từ quy tắc, sản phẩm, bản phục hồi, khóa bảo vệ hoặc lịch sử
thì **không xóa**; dừng để lập phương án riêng, không đổi nhãn thành một
phân loại hợp lệ tùy tiện. Không xóa lịch sử để vượt kiểm tra.

Apply khóa và đọc lại; dữ liệu đổi hoặc phát sinh tham chiếu thì từ chối.
Xóa nhãn và ghi audit trong cùng transaction, giữ bản trước trong audit.
Nếu audit thất bại thì rollback toàn bộ. Sau thành công kiểm hai ID đã mất,
các nhãn khác giữ nguyên và audit có đủ bản trước. Giữ backup trước thao tác;
không dùng audit thay thế backup, không tự khôi phục vào database đang chạy.

## Review chéo và giới hạn được ghi nhận

Claude review chỉ đọc, phiên riêng sau verifier v2: không có finding chặn.

- Công cụ dọn chỉ nhận nhãn số ASCII và đúng ID/nhãn đã duyệt. Nhãn CAS
  Unicode khác nếu có cần phương án riêng; phạm vi hiện tại chỉ hai nhãn ASCII.
- Caller trực tiếp của build_plan có thể truyền CAS có khoảng trắng; đường
  parser đang dùng đã chuẩn hóa. Không mở rộng API nội bộ trong task này.
- Tham chiếu text không có FK và timeout dọn: phải kiểm trước apply như trên.
- Không thêm ràng buộc CAS-only ở database vì cần giữ quy tắc tên/mã lịch sử;
  script SQL ngoài ứng dụng vẫn có thể chèn chúng nhưng resolver bỏ qua.
- Font Awesome CDN là dependency hiện hữu của menu/trang tổng quan; ba trang
  con được bổ sung cùng stylesheet để sửa lỗi biểu tượng trong phạm vi V6.
  Chưa thêm SRI/CSP; đây không phải thay đổi quyền hoặc dependency mới.
- Docstring resolver có câu tiếng Anh thiếu dấu kết câu; không ảnh hưởng runtime.

Nguồn đầy đủ: `specs/regulatory-cas-only/CROSS_VENDOR_REVIEW.md`.
