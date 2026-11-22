// Chạy thử logic của 3 Code node (chuan_hoa_yeu_cau.js, lap_bao_gia_nhap.js, tong_ket.js) bằng Node.js, với
// phản hồi API đã lấy sẵn (xem README). Mô phỏng các hàm $input / $('Node') của n8n.
//   node chay_thu.js yeu_cau_mau.json phan_hoi_api.json ra.json
// phan_hoi_api.json: mảng {statusCode, body} theo đúng thứ tự dòng yêu cầu (dòng không tra cứu = bỏ qua).
const fs = require('fs');
const path = require('path');
const [reqFile, respFile, outFile] = process.argv.slice(2);
const sheet = JSON.parse(fs.readFileSync(reqFile, 'utf8'));
const responses = JSON.parse(fs.readFileSync(respFile, 'utf8'));
const header = sheet[0];
const rows = sheet.slice(1).map((cells) => {
  const obj = {};
  header.forEach((name, i) => { if (cells[i] !== undefined) { obj[name] = cells[i]; } });
  return { json: obj };
});

const load = (name) => fs.readFileSync(path.join(__dirname, '..', name), 'utf8');
const run = (code, ctx) => new Function('$input', '$', code)(ctx.input, ctx.node);

const normalized = run(load('chuan_hoa_yeu_cau.js'), { input: { all: () => rows }, node: () => ({ all: () => [] }) });
const apiItems = normalized.map((it, i) => ({ json: responses[i] || { statusCode: 200, body: { status: 'ok' } } }));
const quote = run(load('lap_bao_gia_nhap.js'), {
  input: { all: () => apiItems },
  node: (name) => ({ all: () => (name === 'Chuan hoa yeu cau' ? normalized : []) }),
});
const summary = run(load('tong_ket.js'), {
  input: { all: () => quote },
  node: (name) => ({ all: () => (name === 'Lap bao gia nhap' ? quote : []) }),
});
fs.writeFileSync(outFile, JSON.stringify({ normalized: normalized.map((x) => x.json), quote: quote.map((x) => x.json), summary: summary.map((x) => x.json) }, null, 2));
console.log('dong yeu cau:', rows.length, '| can tra cuu:', normalized.filter((x) => x.json.can_lookup).length, '| bao gia:', quote.length);
