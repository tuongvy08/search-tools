# Verification Result — <task>

Do verifier độc lập tạo, không do agent chính tự viết. Verifier không thấy lịch sử hội thoại, kế hoạch sửa lỗi hay giải thích của agent chính — chỉ nhận yêu cầu liên quan, VERIFICATION.md đã khóa, diff code, lệnh test, và (nếu có) phần Observed/Expected/Reproduction/Classification của lần fail trước.

## Claim V1

Test thực hiện: <mô tả>
Kết quả: PASS | FAIL
Evidence: <số liệu/log/output cụ thể, trạng thái trước và sau — không phải "đã kiểm tra, ổn">
Negative case đã thử: <ít nhất một tình huống cố làm implementation sai, và kết quả>
Residual risk: <phần chưa được chứng minh, biết rõ còn thiếu gì>

Nếu FAIL, thêm:
Classification: IMPLEMENTATION_FAIL | REGRESSION_FAIL | SPEC_AMBIGUOUS | VERIFICATION_CONTRACT_PROBLEM
Reproduction: <bước lặp lại được>
Suggested area (tùy chọn, KHÔNG phải giải pháp cụ thể): <...>

## Claim V2

<lặp lại cấu trúc trên cho mỗi claim>

## Task status

attempt: <n> / max_auto_repairs: 2
same_claim_repeat_fail: <mã claim nếu một claim fail 2 lần liên tiếp, hoặc "none">
next_action: HUMAN_REVIEW | CODER_REPAIR | ESCALATE_USER
