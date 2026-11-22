# n8n — lập báo giá nháp từ Google Sheet (staging)

Workflow: **Search-tools - Bao gia nhap tu dong (staging)** (n8n, ID `cJxA0CCxw2gAiZ3S`). Doc tab `Yeu_cau` của Google Sheet thử → tra cứu cổng API chỉ-đọc bằng **thẻ nội bộ** (có giá) → ghi tab `Bao_gia_nhap` (ghi lại/ cập nhật theo `STT`, chạy lại không tạo dòng trùng) và `Tong_ket`.

## Quy tắc (PO 2026-11-21)

- Chỉ **tự điền** mã/giá khi xác định được **đúng một** sản phẩm (mã/CAS khớp chính xác, hoặc tên khớp chính xác + lọc hãng/quy cách). Còn lại: `CẦN NGƯỜI XỬ LÝ` + lý do + 5 ứng viên gợi ý; **không** lấy ứng viên đầu tiên.
- Không cộng lợi nhuận. Giá = giá VND đúng công thức ứng dụng nhân viên; cột `Gia_goc`, `He_so_ship`, `Tien_te`, `Ty_gia_VND` ghi kèm.
- Pháp chế chặn báo giá (Cấm nhập...) → không tính thành tiền. "Chưa có quy tắc" luôn ghi kèm câu cần xác nhận với pháp chế.
- Số lượng sai/thiếu, dòng trống, CAS sai định dạng, trùng dòng: ghi nhận, không bỏ mất dòng nào.

## Gắn credential (1 lần, ~2 phút)

Workflow dùng credential **`Search_Tools_Machine_API_Noi_bo`** (chưa tồn tại — phải tạo): n8n → Credentials → *Custom Auth (templated)* (hoặc *Header Auth* nếu bản n8n của bạn không có): header `Authorization` = `Bearer <thẻ nội bộ>`. Thẻ nội bộ do công cụ cấp thẻ in ra **một lần** (xem [OPERATIONS mục 7](../../../docs/machine-api/OPERATIONS.md)). **Không** dùng thẻ của chatbot khách (không có giá). Trước khi lên production phải dùng credential, không đặt thẻ trong cấu hình node.

## Kiểm thử logic không cần n8n

```bash
node integrations/n8n/bao-gia-tu-dong/test/chay_thu.js yeu_cau_mau.json phan_hoi_api.json ra.json
```

`tao_workflow_sdk.py` sinh mã SDK của workflow từ 3 file JS (`chuan_hoa_yeu_cau.js`, `lap_bao_gia_nhap.js`, `tong_ket.js`) — sửa JS rồi sinh lại, không sửa tay trong n8n.
