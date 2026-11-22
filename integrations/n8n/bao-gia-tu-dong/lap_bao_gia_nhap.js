// n8n Code node "Lap bao gia nhap" (Run Once for All Items), chạy SAU node HTTP Request "Tra san pham".
// Đầu vào: phản hồi API (fullResponse) theo đúng thứ tự dòng của node "Chuan hoa yeu cau".
// Quy tắc (PO 2026-11-21): chỉ TỰ ĐIỀN khi xác định được ĐÚNG MỘT sản phẩm; mọi trường hợp còn lại
// ghi "CẦN NGƯỜI XỬ LÝ" kèm lý do, để trống mã/giá, không bao giờ lấy ứng viên đầu tiên. Không cộng lợi nhuận.
const STATUS_OK = 'ĐÃ KHỚP';
const STATUS_REVIEW = 'CẦN NGƯỜI XỬ LÝ';
const STATUS_SKIP = 'BỎ QUA';
const norm = (v) => (v === null || v === undefined) ? '' : String(v).trim();
const up = (v) => norm(v).toUpperCase();
const compact = (v) => norm(v).toLowerCase().replace(/ +/g, '');
const NL = String.fromCharCode(10);
const money = (n) => Math.round(n).toLocaleString('en-US');
const numOrText = (v) => {
  const t = norm(v);
  return (t !== '' && !isNaN(Number(t))) ? Number(t) : t;
};
const describe = (r) => {
  const parts = [];
  if (r.mode === 'code') { parts.push('Mã: ' + r.q); }
  if (r.mode === 'cas') { parts.push('CAS: ' + r.q); }
  if (r.mode === 'name') { parts.push('Tên: ' + r.q); }
  if (r.brand) { parts.push('Hãng: ' + r.brand); }
  if (r.size) { parts.push('Quy cách: ' + r.size); }
  return parts.join(' | ');
};
const candidateText = (p) => {
  const pr = p.price || {};
  const price = (typeof pr.unit_price_vnd === 'number' && pr.unit_price_vnd > 0) ? money(pr.unit_price_vnd) + ' đ' : 'chưa có giá';
  return [p.code, p.brand, p.size, price].join(' | ');
};
const complianceLabel = (c) => {
  if (c.state === 'RULE') { return c.label; }
  if (c.state === 'NO_RULE') { return 'Chưa có quy tắc'; }
  return c.state || '';
};

const reqs = $('Chuan hoa yeu cau').all();
const resp = $input.all();
const nowText = new Date().toISOString();
const out = [];
for (let i = 0; i < reqs.length; i++) {
  const r = reqs[i].json;
  const res = resp[i] ? resp[i].json : {};
  const row = {
    STT: r.STT, Trang_thai: '', Ly_do_can_xu_ly: '', Yeu_cau_goc: describe(r), Ma_hang: '', Ten_san_pham: '',
    Hang: '', Quy_cach: '', So_luong: (r.qty === null || r.qty === undefined) ? '' : r.qty, Gia_goc: '', He_so_ship: '',
    Tien_te: '', Ty_gia_VND: '', Don_gia_VND: '', Thanh_tien_VND: '', Ton_kho: '', So_luong_ton: '', Du_hang: '',
    Han_dung_gan_nhat: '', Phap_che_nhan: '', Chan_bao_gia: '', Ghi_chu_phap_che: '', So_ung_vien: '',
    Ung_vien_goi_y: '', Ghi_chu_he_thong: '', Chay_luc: nowText,
  };
  const reasons = [];
  const notes = [];
  if (r.dupOf) { notes.push('Trùng với dòng ' + r.dupOf); }
  const finish = (status) => {
    row.Trang_thai = status;
    row.Ly_do_can_xu_ly = reasons.join('; ');
    row.Ghi_chu_he_thong = notes.join('; ');
    out.push({ json: row });
  };

  if (r.mode === 'none') {
    reasons.push(r.skipReason);
    finish(STATUS_SKIP);
    continue;
  }
  if (r.qtyError) { reasons.push(r.qtyError); }
  if (r.skipReason) {
    reasons.unshift(r.skipReason);
    finish(STATUS_REVIEW);
    continue;
  }
  const body = res.body;
  if (res.statusCode !== 200 || !body || !Array.isArray(body.results)) {
    reasons.unshift('Không gọi được API tra cứu (mã ' + (res.statusCode || 'lỗi mạng') + ')');
    finish(STATUS_REVIEW);
    continue;
  }

  let cands = body.results;
  let exactName = false;
  if (r.mode === 'code') {
    cands = cands.filter((p) => up(p.code) === up(r.q));
  } else if (r.mode === 'cas') {
    cands = cands.filter((p) => up(p.cas) === up(r.q));
  } else {
    const exact = cands.filter((p) => up(p.name) === up(r.q));
    if (exact.length > 0) { cands = exact; exactName = true; }
  }
  if (r.brand) {
    const byBrand = cands.filter((p) => up(p.brand) === up(r.brand));
    if (byBrand.length > 0) {
      cands = byBrand;
    } else if (cands.length > 0) {
      const brands = [];
      cands.forEach((p) => { if (brands.indexOf(p.brand) < 0 && brands.length < 3) { brands.push(p.brand); } });
      reasons.push('Hãng yêu cầu "' + r.brand + '" không khớp (hệ thống có: ' + brands.join(', ') + ')');
    }
  }
  if (r.size) {
    const bySize = cands.filter((p) => compact(p.size) === compact(r.size));
    if (bySize.length > 0) {
      cands = bySize;
    } else if (cands.length > 0) {
      reasons.push('Quy cách yêu cầu "' + r.size + '" không khớp');
    }
  }
  row.So_ung_vien = cands.length;

  let selected = null;
  if (cands.length === 1 && (r.mode !== 'name' || exactName)) {
    selected = cands[0];
  } else if (cands.length === 0) {
    reasons.unshift('Không tìm thấy sản phẩm khớp');
  } else {
    reasons.unshift('Có ' + cands.length + (body.more ? '+' : '') + ' ứng viên, cần người chọn đúng hãng/quy cách' +
      ((r.mode === 'name' && !exactName) ? ' (tên chỉ khớp một phần)' : ''));
    row.Ung_vien_goi_y = cands.slice(0, 5).map(candidateText).join(NL);
    if (r.mode === 'cas') {
      const c0 = cands[0].compliance || {};
      row.Phap_che_nhan = complianceLabel(c0) + ' (theo CAS)';
      row.Chan_bao_gia = c0.blocked ? 'CÓ' : 'Không';
      row.Ghi_chu_phap_che = c0.state === 'NO_RULE' ? 'Chưa có quy tắc — KHÔNG có nghĩa được phép nhập; cần pháp chế xác nhận' : (c0.regulatory_note || '');
      if (c0.blocked) { reasons.push('Pháp chế (theo CAS): ' + c0.label + ' — chặn báo giá'); }
    }
  }

  if (selected !== null) {
    const p = selected;
    const pr = p.price || {};
    row.Ma_hang = p.code;
    row.Ten_san_pham = p.name;
    row.Hang = p.brand;
    row.Quy_cach = p.size;
    row.Gia_goc = numOrText(pr.original_price);
    row.He_so_ship = numOrText(pr.ship_factor);
    row.Tien_te = pr.currency || '';
    row.Ty_gia_VND = (pr.rate_vnd === null || pr.rate_vnd === undefined) ? '' : pr.rate_vnd;
    if (pr.unit_price_vnd === null || pr.unit_price_vnd === undefined) {
      reasons.push('Không quy đổi được giá (' + pr.price_status + ')');
    } else if (pr.price_status !== 'OK' || !(pr.unit_price_vnd > 0)) {
      reasons.push('Sản phẩm chưa có giá trong hệ thống (giá liên hệ)');
    } else {
      row.Don_gia_VND = pr.unit_price_vnd;
    }
    const c = p.compliance || {};
    row.Phap_che_nhan = complianceLabel(c);
    row.Chan_bao_gia = c.blocked ? 'CÓ' : 'Không';
    row.Ghi_chu_phap_che = c.state === 'NO_RULE' ? 'Chưa có quy tắc — KHÔNG có nghĩa được phép nhập; cần pháp chế xác nhận' : (c.regulatory_note || '');
    if (c.blocked) {
      reasons.push('Pháp chế chặn báo giá: ' + (c.label || c.state));
    } else if (c.state === 'DATA_ERROR') {
      reasons.push('Dữ liệu pháp chế đang lỗi, cần kiểm tra');
    }
    const s = p.stock || {};
    row.Ton_kho = s.status === 'IN_STOCK' ? 'Còn hàng' : 'Hết hàng';
    row.So_luong_ton = (typeof s.quantity === 'number') ? s.quantity : '';
    if (r.qty !== null && r.qty !== undefined && typeof s.quantity === 'number') {
      row.Du_hang = s.quantity <= 0 ? 'HẾT HÀNG' : (s.quantity >= r.qty ? 'ĐỦ' : 'THIẾU (còn ' + s.quantity + ')');
    }
    const expiries = (s.lots || []).filter((l) => l.available && l.expiry_date).map((l) => l.expiry_date).sort();
    row.Han_dung_gan_nhat = expiries.length > 0 ? expiries[0] : '';
    if (s.status === 'IN_STOCK' && s.pack_confirmed === false) { notes.push('Tồn kho khớp theo mã, hãng/quy cách chưa xác nhận'); }
    if (s.expiry_warning) { notes.push('Hàng sắp hết hạn dùng'); }
    if (reasons.length === 0 && typeof row.So_luong === 'number' && typeof row.Don_gia_VND === 'number') {
      row.Thanh_tien_VND = Math.round(row.So_luong * row.Don_gia_VND);
    }
  }
  finish(reasons.length > 0 ? STATUS_REVIEW : STATUS_OK);
}
return out;
