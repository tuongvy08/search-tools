# Verification Contract — <task>

Tạo TRƯỚC khi implement. Sau khi implement bắt đầu, file này là hợp đồng đã khóa — xem "Thay đổi hợp đồng" ở cuối file.

## Claim V1

<Mô tả một hành vi hệ thống phải đảm bảo, viết theo nghiệp vụ, không viết theo cách implement.>

Oracle:
<Cách khách quan để biết claim đúng/sai — số liệu, trạng thái database, response cụ thể, không phải "AI nói pass".>

Test type: <unit / integration / manual UAT>

Failure case cần thử:
<Tình huống cụ thể có khả năng làm claim sai — không chỉ happy path.>

## Claim V2

<lặp lại cấu trúc trên cho mỗi claim>

## Ngoài phạm vi

<Những gì task này không cần đảm bảo, để verifier không đi kiểm tra thứ không liên quan.>

---

## Thay đổi hợp đồng

Không tự sửa file này sau khi implement đã bắt đầu để làm test dễ pass hơn. Cần thay đổi claim hoặc oracle:

1. Ghi lý do thay đổi vào "Change request log" dưới đây.
2. Xin phê duyệt — người dùng cho thay đổi yêu cầu; verifier độc lập đủ nếu chỉ làm rõ oracle, không đổi yêu cầu.
3. Tăng version, ví dụ đổi tiêu đề thành "VERIFICATION.md v2".
4. Chạy lại toàn bộ verification từ đầu với version mới — không tính là repair của version cũ.

### Change request log

<để trống nếu chưa có thay đổi>
