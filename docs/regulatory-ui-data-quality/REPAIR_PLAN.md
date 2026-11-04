# Phương án xử lý sau đối chiếu web — 2026-11-02

**ĐỀ XUẤT BÊN DƯỚI ĐÃ THAY THẾ, CHƯA TRIỂN KHAI.** PO đã làm rõ:
quy tắc tự động chỉ CAS; gán trực tiếp tình trạng sản phẩm chỉ ghi đè khi
hãng ưu tiên thủ công. Không làm ngoại lệ riêng theo CAS cho quy tắc tên.
Hợp đồng hiện hành: `specs/regulatory-cas-only/VERIFICATION.md`.

Trạng thái: **đề xuất, chờ PO duyệt phần mở rộng HIGH**. Không phải bằng
chứng đã sửa dữ liệu hoặc đã phát hành.

## Kết quả đọc giao diện trong phiên PO đăng nhập

- Quy tắc tên `Diethyl ether` (#6010, revision 1) đang áp dụng và chưa có lịch sử sửa tay.
  Ghi chú/tình trạng trùng nội dung cảnh báo PO báo lỗi. Cách khớp chuỗi
  con trong code nhận cả `Ethylene Glycol Diethyl Ether`.
- Danh mục có 13 dòng sản phẩm với CAS được báo lỗi; cả 13 hiển thị quản
  lý tự động, trong đó 9 tên chứa chuỗi trên. Như vậy phạm vi không chỉ hai
  dòng trong ảnh. Không chép giá hoặc thông tin tài khoản vào hồ sơ này.
- Đã có quy tắc CAS riêng cho Diethyl ether (#5825, CAS 60-29-7) đang áp dụng.
  Quy tắc Được bán cho CAS PO báo lỗi (#17408, revision 2) đã được thêm rồi
  ngừng áp dụng trước phiên kiểm tra.
  Bật lại quy tắc Được bán không giải quyết việc ưu tiên trạng thái cao hơn.
- Tìm sản phẩm theo tên Diethyl ether cho ít nhất 100 kết quả, có các chất
  khác mang tên chứa chuỗi này, có dung dịch dùng Diethyl ether làm dung
  môi và có dòng thiếu CAS. Chưa thống kê toàn bộ số trang. Do đó không
  thể coi tắt quy tắc tên hoặc chỉ xét CAS là thay đổi không có ảnh hưởng phụ.
- Hai tình trạng mang tên CAS (ID 8 và 9) không có quy tắc liên kết khi lọc mọi trạng
  thái áp dụng. Các nhóm sản phẩm có đúng hai CAS đó lần lượt có 39 và 16
  dòng, tất cả hiển thị Tự động. Điều này **không chứng minh** hai tình trạng
  không được gán cho sản phẩm CAS khác hoặc còn tham chiếu lịch sử.
- Giao diện hiện không có chức năng xóa/ngừng tình trạng hoặc lọc sản phẩm
  theo định danh tình trạng thủ công; chưa thể kết luận an toàn để xóa.

## Phương án khuyến nghị cần duyệt

1. Bổ sung danh sách CAS loại trừ cho **từng quy tắc theo tên**. Áp dụng
   ban đầu chỉ cho cặp quy tắc `Diethyl ether` và CAS `629-14-1` theo yêu
   cầu nghiệp vụ của PO. Không gán Được bán, không loại trừ quy tắc khác,
   không tắt cảnh báo cho dung dịch hoặc sản phẩm thiếu CAS.
2. Ngoại lệ là dữ liệu cấu hình có lịch sử, không hardcode tên/CAS vào code.
   Form có xem trước/sau, lý do bắt buộc, revision chống ghi đè, cùng quyền
   regulatory. Thêm/sửa ngoại lệ đánh dấu quy tắc được bảo vệ khi import;
   importer, fingerprint và audit phải nhận biết thay đổi ngoại lệ.
3. Migration thêm dữ liệu ngoại lệ với mặc định rỗng; ban đầu không đổi kết
   quả của bất kỳ quy tắc nào. Kiểm trên DB thử riêng, verifier độc lập bằng
   Claude theo workflow; chưa truy cập DB/server production.
4. Hai nhãn CAS sai: chuẩn bị kiểm tra chỉ đọc tất cả tham chiếu hiện tại và
   lịch sử theo schema thật. Chỉ đề xuất xóa chính xác hai mục khi không có
   tham chiếu; nếu có thì dừng và lập bản chuyển đúng tình trạng để PO duyệt.
   Không đổi nhãn thành Được bán/Chưa xác định để che dữ liệu sai.
5. Chạy staging/UAT, trình bản trước/sau và kế hoạch backup/khôi phục trước
   bước production. Phê duyệt làm local không tự cấp quyền commit/push/deploy
   hoặc thay đổi dữ liệu thật.

## Nghiệm thu quan sát được

- CAS 629-14-1 không còn nhận cảnh báo **từ quy tắc tên Diethyl ether**;
  nếu có quy tắc quản lý khác thì vẫn hiển thị đúng quy tắc đó.
- Diethyl ether thật và dung dịch/nhóm khác vẫn có kết quả như trước.
- Nhập lại Excel không xóa hoặc làm mất ngoại lệ đã sửa thủ công.
- Hai CAS không còn xuất hiện như tình trạng mới sau khi xử lý dữ liệu được
  duyệt; các tên tình trạng do admin tự đặt vẫn sử dụng bình thường.
- Người quản trị nhìn được trước/sau và lịch sử thay đổi.

Hợp đồng kiểm chứng dự thảo: `specs/regulatory-targeted-exclusion/VERIFICATION.md`.
Không kết luận pháp lý về hóa chất; đây là phương án xử lý quy tắc ứng dụng.
