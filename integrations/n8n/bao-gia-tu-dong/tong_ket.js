// n8n Code node "Tong ket" (Run Once for All Items): đếm kết quả để ghi vào tab Tong_ket.
const rows = $('Lap bao gia nhap').all().map((it) => it.json);
const count = (status) => rows.filter((r) => r.Trang_thai === status).length;
const nowText = new Date().toISOString();
const total = rows.filter((r) => typeof r.Thanh_tien_VND === 'number').reduce((sum, r) => sum + r.Thanh_tien_VND, 0);
const items = [
  ['Tong_so_dong_yeu_cau', rows.length],
  ['DA_KHOP', count('ĐÃ KHỚP')],
  ['CAN_NGUOI_XU_LY', count('CẦN NGƯỜI XỬ LÝ')],
  ['BO_QUA_dong_trong', count('BỎ QUA')],
  ['Dong_co_phap_che_chan', rows.filter((r) => r.Chan_bao_gia === 'CÓ').length],
  ['Dong_thieu_ton_kho', rows.filter((r) => String(r.Du_hang).indexOf('THIẾU') === 0 || r.Du_hang === 'HẾT HÀNG').length],
  ['Tong_thanh_tien_cac_dong_DA_KHOP_VND', total],
];
return items.map((x) => ({ json: { Chi_so: x[0], Gia_tri: x[1], Chay_luc: nowText } }));
