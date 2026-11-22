// n8n Code node "Chuan hoa yeu cau" (Run Once for All Items).
// Đầu vào: các dòng tab Yeu_cau của Google Sheet. Đầu ra: mỗi dòng 1 item đã chuẩn hóa, sẵn sàng tra cứu.
// Không gọi mạng ở đây (Code node của n8n không có mạng); việc gọi API do node HTTP Request làm.
const norm = (v) => (v === null || v === undefined) ? '' : String(v).trim();
const rows = $input.all();
const seen = {};
const out = [];
rows.forEach((it, idx) => {
  const j = it.json;
  const stt = norm(j.STT) || String(idx + 1);
  const code = norm(j.Ma_hang);
  const cas = norm(j.CAS);
  const name = norm(j.Ten_san_pham);
  const brand = norm(j.Hang);
  const size = norm(j.Quy_cach);
  const qtyText = norm(j.So_luong).replace(',', '.');
  let qty = null;
  let qtyError = '';
  if (qtyText === '') {
    qtyError = 'Thiếu số lượng';
  } else if (!/^[0-9]+([.][0-9]+)?$/.test(qtyText)) {
    qtyError = 'Số lượng không hợp lệ: ' + qtyText;
  } else {
    qty = Number(qtyText);
    if (!(qty > 0)) {
      qtyError = 'Số lượng phải lớn hơn 0';
    }
  }
  const casValid = /^[0-9]{2,7}-[0-9]{2}-[0-9]$/.test(cas);
  let mode = 'none';
  let q = '';
  let skipReason = '';
  if (code) {
    mode = 'code';
    q = code;
  } else if (cas) {
    mode = 'cas';
    q = cas;
    if (!casValid) {
      skipReason = 'CAS sai định dạng (cần dạng 64-17-5): ' + cas;
    }
  } else if (name) {
    mode = 'name';
    q = name;
  } else {
    skipReason = 'Dòng trống: không có mã hàng, CAS hay tên sản phẩm';
  }
  if (mode !== 'none' && !skipReason && q.length < 3) {
    skipReason = 'Từ khóa quá ngắn (tối thiểu 3 ký tự): ' + q;
  }
  if (q.length > 100) {
    skipReason = 'Từ khóa quá dài (tối đa 100 ký tự)';
  }
  const key = mode + '|' + q.toUpperCase().replace(/ +/g, ' ') + '|' + brand.toUpperCase() + '|' + size.toUpperCase();
  let dupOf = '';
  if (mode !== 'none') {
    if (seen[key]) {
      dupOf = seen[key];
    } else {
      seen[key] = stt;
    }
  }
  out.push({
    json: {
      STT: stt, mode: mode, q: q, code: code, cas: cas, name: name, brand: brand, size: size,
      qty: qty, qtyError: qtyError, skipReason: skipReason, dupOf: dupOf,
      can_lookup: mode !== 'none' && !skipReason,
    },
  });
});
return out;
