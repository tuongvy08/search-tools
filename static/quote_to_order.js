/* Báo giá → Đơn hàng. Trình duyệt giữ file + dữ liệu; server chỉ đọc file (/parse) và tạo file đơn hàng (/export).
   Mọi dữ liệu từ file đưa lên trang bằng textContent/value, không dùng innerHTML. */
(function () {
  'use strict';

  var CONFIG = JSON.parse(document.getElementById('qtoConfig').textContent);
  var CSRF = JSON.parse(document.getElementById('qtoCsrf').textContent);
  var FIELDS = CONFIG.fields;
  var FIELD_BY_KEY = {};
  FIELDS.forEach(function (f) { FIELD_BY_KEY[f.key] = f; });
  var NUM_KEYS = FIELDS.filter(function (f) { return f.kind === 'num'; }).map(function (f) { return f.key; });
  var MEMORY_KEY = 'qto.mapping.v1';

  /* Cột của bảng, theo thứ tự hiển thị. */
  var COLUMNS = [
    ['name', 'Tên hàng', 'wide'], ['code', 'Code', ''], ['cas', 'Cas', ''], ['brand', 'Hãng', ''], ['unit', 'ĐVT', 'narrow'],
    ['qty', 'Số lượng', 'num'], ['price', 'Đơn giá (có VAT)', 'num'], ['amount', 'Thành tiền', 'num readonly'],
    ['cost', 'Giá mua dự kiến', 'num'], ['type', 'Loại hàng', ''], ['note_goods', 'Ghi chú về hàng hóa', 'wide'],
    ['note_other', 'Ghi chú khác', 'wide'], ['min_price', 'Giá bán tối thiểu', 'num']
  ];

  var state = { files: [], rows: [], nextId: 1 };
  var $ = function (id) { return document.getElementById(id); };
  var els = {
    drop: $('qtoDrop'), file: $('qtoFile'), messages: $('qtoMessages'), files: $('qtoFiles'),
    listCard: $('qtoListCard'), filter: $('qtoFilter'), head: $('qtoHead'), body: $('qtoBody'),
    summary: $('qtoSummary'), download: $('qtoDownload'), blockers: $('qtoBlockers')
  };

  /* ---------- tiện ích ---------- */
  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') node.textContent = attrs[k];
      else if (k === 'class') node.className = attrs[k];
      else if (attrs[k] === true) node.setAttribute(k, '');
      else if (attrs[k] !== false && attrs[k] != null) node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) node.appendChild(c); });
    return node;
  }

  /* Cùng quy tắc với server: '1.250.000' -> 1250000, '0,5' -> 0.5, '1.5' -> 1.5. Trả null nếu rỗng, NaN nếu sai. */
  function parseNum(text) {
    if (text == null) return null;
    var s = String(text).replace(/[\s ]/g, '').replace(/(đ|vnđ|vnd)$/i, '');
    if (s === '') return null;
    var sign = '';
    if (s[0] === '-' || s[0] === '+') { sign = s[0]; s = s.slice(1); }
    if (!/^[\d.,]+$/.test(s)) return NaN;
    if (s.indexOf('.') >= 0 && s.indexOf(',') >= 0) {
      var dec = s.lastIndexOf('.') > s.lastIndexOf(',') ? '.' : ',';
      var th = dec === '.' ? ',' : '.';
      s = s.split(th).join('').replace(dec, '.');
    } else if (/^\d{1,3}([.,]\d{3})+$/.test(s) && s[0] !== '0') {
      s = s.replace(/[.,]/g, '');
    } else {
      if ((s.match(/[.,]/g) || []).length > 1) return NaN;
      s = s.replace(',', '.');
    }
    var n = Number(sign + s);
    return isFinite(n) ? n : NaN;
  }
  function fmtNum(n) {
    return (n == null || isNaN(n)) ? '' : n.toLocaleString('vi-VN', { maximumFractionDigits: 6 });
  }
  function fmtMoney(n) { return n.toLocaleString('vi-VN', { maximumFractionDigits: 2 }) + ' đ'; }
  function plain(n) { return n == null ? '' : String(n).replace('.', ','); }

  function hashString(s) {
    var h = 5381;
    for (var i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
    return String(h >>> 0);
  }

  /* ---------- nhớ cách ghép cột (trình duyệt của từng người) ---------- */
  function memoryRead() {
    try { return JSON.parse(localStorage.getItem(MEMORY_KEY) || '{}') || {}; } catch (e) { return {}; }
  }
  function memoryWrite(data) {
    try { localStorage.setItem(MEMORY_KEY, JSON.stringify(data)); } catch (e) { /* bỏ qua: không lưu được cũng không sao */ }
  }
  function signatureOf(columns) {
    return hashString(columns.map(function (c) { return c.title; }).join('|'));
  }
  function rememberMapping(meta) {
    var data = memoryRead();
    var titles = {};
    FIELDS.forEach(function (f) {
      var idx = meta.mapping[f.key];
      var col = meta.columns.filter(function (c) { return c.index === idx; })[0];
      titles[f.key] = col ? col.title : null;
    });
    data[signatureOf(meta.columns)] = { t: Date.now(), titles: titles, hr: meta.header_row };
    var keys = Object.keys(data).sort(function (a, b) { return data[b].t - data[a].t; });
    keys.slice(30).forEach(function (k) { delete data[k]; });
    memoryWrite(data);
  }
  function recalledMapping(meta) {
    var entry = memoryRead()[signatureOf(meta.columns)];
    if (!entry || !entry.titles) return null;
    var mapping = {};
    FIELDS.forEach(function (f) {
      var title = entry.titles[f.key];
      var col = title == null ? null : meta.columns.filter(function (c) { return c.title === title; })[0];
      mapping[f.key] = col ? col.index : null;
    });
    return mapping;
  }

  /* ---------- thông báo ---------- */
  function say(text, level) {
    var box = el('div', { class: 'state-banner ' + (level === 'error' ? 'state-error' : level === 'ok' ? 'state-success' : 'state-notice') });
    box.appendChild(el('span', { text: text }));
    var close = el('button', { type: 'button', class: 'secondary qto-dismiss', 'aria-label': 'Đóng thông báo', text: '×' });
    close.addEventListener('click', function () { box.remove(); });
    box.appendChild(close);
    els.messages.appendChild(box);
    return box;
  }

  /* ---------- gọi server ---------- */
  function postParse(file, opts) {
    var form = new FormData();
    form.append('quote', file, file.name);
    if (opts && opts.mapping) form.append('mapping', JSON.stringify(opts.mapping));
    if (opts && opts.headerRow) form.append('header_row', String(opts.headerRow));
    if (opts && opts.sheet) form.append('sheet', opts.sheet);
    return fetch(CONFIG.urls.parse, { method: 'POST', body: form, credentials: 'same-origin', headers: { 'X-CSRF-Token': CSRF } })
      .then(readJson);
  }
  function readJson(resp) {
    return resp.json().catch(function () { return { ok: false, error: 'Máy chủ trả về dữ liệu không hợp lệ (mã ' + resp.status + ').' }; })
      .then(function (data) { data.status = resp.status; return data; });
  }

  /* ---------- tải file lên ---------- */
  function addFiles(fileList) {
    var list = Array.prototype.slice.call(fileList);
    var chain = Promise.resolve();
    list.forEach(function (file) { chain = chain.then(function () { return addOneFile(file); }); });
    chain.then(refresh);
  }

  function addOneFile(file) {
    if (!/\.xlsx$/i.test(file.name)) { say('"' + file.name + '" không phải file .xlsx.', 'error'); return Promise.resolve(); }
    if (file.size > CONFIG.limits.maxFileBytes) {
      say('"' + file.name + '" quá lớn (tối đa ' + Math.floor(CONFIG.limits.maxFileBytes / 1048576) + 'MB).', 'error');
      return Promise.resolve();
    }
    if (state.files.length >= CONFIG.limits.maxFiles) {
      say('Chỉ được tải tối đa ' + CONFIG.limits.maxFiles + ' file mỗi lần.', 'error');
      return Promise.resolve();
    }
    return postParse(file).then(function (res) {
      if (!res.ok) { say(res.error || 'Không đọc được file.', 'error'); return; }
      var entry = { id: state.nextId++, file: file, name: file.name, meta: res, open: false, edited: false };
      return applyRemembered(entry).then(function () { return finishAdd(entry); });
    }).catch(function () { say('Không gửi được file "' + file.name + '". Kiểm tra kết nối rồi thử lại.', 'error'); });
  }

  /* Nếu mẫu này đã từng được ghép cột trên trình duyệt này thì áp dụng lại (kể cả mẫu mà dòng tiêu đề không tự tìm được). */
  function applyRemembered(entry) {
    var res = entry.meta;
    if (!res.needs_header) return tryMemory(entry, res);
    var data = memoryRead();
    var rows = Object.keys(data).sort(function (a, b) { return data[b].t - data[a].t; })
      .map(function (k) { return data[k].hr; }).filter(function (hr, i, a) { return hr && a.indexOf(hr) === i; }).slice(0, 3);
    var chain = Promise.resolve(false);
    rows.forEach(function (hr) {
      chain = chain.then(function (done) {
        if (done) return true;
        return postParse(entry.file, { headerRow: hr, sheet: res.sheet }).then(function (cand) {
          return cand.ok && !cand.needs_header && recalledMapping(cand) ? tryMemory(entry, cand).then(function () { return true; }) : false;
        });
      });
    });
    return chain;
  }

  function tryMemory(entry, res) {
    var remembered = recalledMapping(res);
    if (!remembered) { entry.meta = res; return Promise.resolve(); }
    if (JSON.stringify(remembered) === JSON.stringify(res.mapping)) { entry.meta = res; entry.remembered = true; return Promise.resolve(); }
    return postParse(entry.file, { mapping: remembered, headerRow: res.header_row, sheet: res.sheet }).then(function (again) {
      entry.meta = again.ok ? again : res;
      entry.remembered = !!again.ok;
    });
  }

  function finishAdd(entry) {
    var meta = entry.meta;
    if (state.rows.length + meta.items.length > CONFIG.limits.maxRows) {
      say('Thêm "' + entry.name + '" sẽ vượt quá ' + CONFIG.limits.maxRows + ' dòng cho một file đơn hàng, nên không được thêm.', 'error');
      return;
    }
    state.files.push(entry);
    entry.open = needsAttention(meta);
    if (meta.needs_header) entry.notice = say(entry.name + ': ' + meta.message, 'warn');
    else if (meta.header_mismatch) entry.notice = say('"' + entry.name + '": dòng tiêu đề ở dòng ' + meta.header_row + ', khác mặc định (dòng ' + meta.default_header_row +
      '). Hãy kiểm tra các cột đã nhận diện, hoặc sửa số dòng tiêu đề rồi bấm Áp dụng.', 'warn');
    else if (!meta.items.length) say('"' + entry.name + '" không có dòng hàng nào theo cách ghép cột hiện tại.', 'warn');
    appendRows(entry);
  }

  function newRow(source, values) {
    var row = { id: state.nextId++, fileId: source ? source.id : null, sourceName: source ? source.name : 'Nhập tay',
      sourceRow: values && values.source_row || null, selected: false, raw: {} };
    FIELDS.forEach(function (f) {
      var v = values ? values[f.key] : null;
      row.raw[f.key] = v == null ? '' : (f.kind === 'num' ? plain(v) : String(v));
    });
    return row;
  }

  function appendRows(entry) {
    entry.meta.items.forEach(function (item) { state.rows.push(newRow(entry, item)); });
  }

  function replaceRows(entry) {
    state.rows = state.rows.filter(function (r) { return r.fileId !== entry.id; });
    appendRows(entry);
    entry.edited = false;
  }

  function reparse(entry, opts) {
    return postParse(entry.file, opts).then(function (res) {
      if (!res.ok) { say(res.error || 'Không đọc được file.', 'error'); return false; }
      entry.meta = res;
      if (entry.notice && !res.needs_header && !res.header_mismatch) { entry.notice.remove(); entry.notice = null; }
      replaceRows(entry);
      return true;
    }).catch(function () { say('Không gửi được file. Kiểm tra kết nối rồi thử lại.', 'error'); return false; });
  }

  /* ---------- panel ghép cột ---------- */
  function needsAttention(meta) {
    return !!(meta.needs_header || meta.header_mismatch || (meta.unmapped_required && meta.unmapped_required.length));
  }

  function mappedCount(meta) {
    return FIELDS.filter(function (f) { return meta.mapping && meta.mapping[f.key] != null; }).length;
  }

  function renderFiles() {
    els.files.textContent = '';
    state.files.forEach(function (entry) { els.files.appendChild(renderFileCard(entry)); });
  }

  function renderFileCard(entry) {
    var meta = entry.meta;
    var count = state.rows.filter(function (r) { return r.fileId === entry.id; }).length;
    var needs = needsAttention(meta);
    var head = el('div', { class: 'qto-file-head' });
    head.appendChild(el('strong', { text: entry.name }));
    head.appendChild(el('span', { class: 'muted', text: meta.needs_header ? 'Chưa tìm thấy dòng tiêu đề'
      : count + ' dòng hàng · sheet “' + meta.sheet + '” · tiêu đề ở dòng ' + meta.header_row }));
    var badge = el('span', { class: 'qto-badge ' + (needs ? 'warn' : 'ok'),
      text: meta.needs_header ? 'Cần chọn dòng tiêu đề'
        : meta.header_mismatch ? 'Tiêu đề ở dòng ' + meta.header_row + ' (mặc định ' + meta.default_header_row + ') — kiểm tra lại'
        : needs ? 'Còn ' + meta.unmapped_required.length + ' cột bắt buộc chưa nhận diện'
        : 'Đã nhận diện ' + mappedCount(meta) + '/' + FIELDS.length + ' cột' });
    head.appendChild(badge);
    var toggle = el('button', { type: 'button', class: 'secondary', text: entry.open ? 'Thu gọn' : 'Kiểm tra / sửa ghép cột', 'aria-expanded': entry.open ? 'true' : 'false' });
    toggle.addEventListener('click', function () { entry.open = !entry.open; renderFiles(); });
    var remove = el('button', { type: 'button', class: 'secondary', text: 'Bỏ file' });
    remove.addEventListener('click', function () { removeFile(entry); });
    head.appendChild(toggle);
    head.appendChild(remove);
    var card = el('div', { class: 'qto-file' }, [head]);
    if (!meta.needs_header && !entry.open) {
      var unmapped = FIELDS.filter(function (f) { return meta.mapping[f.key] == null; }).map(function (f) { return f.label; });
      card.appendChild(el('p', { class: 'muted qto-note', text: unmapped.length
        ? 'Chưa ghép cột cho: ' + unmapped.join(', ') + '. Bấm “Kiểm tra / sửa ghép cột” nếu cần chọn lại.'
        : 'Đã ghép đủ các cột. Bấm “Kiểm tra / sửa ghép cột” nếu muốn xem lại.' }));
    }
    if (entry.remembered) card.appendChild(el('p', { class: 'muted qto-note', text: 'Đã áp dụng cách ghép cột bạn chọn lần trước cho mẫu này.' }));
    if (entry.open) card.appendChild(renderMappingPanel(entry));
    return card;
  }

  function renderMappingPanel(entry) {
    var meta = entry.meta;
    var panel = el('div', { class: 'qto-mapping' });
    var controls = el('div', { class: 'qto-map-top' });
    var sheetSelect = null;
    if (meta.sheets && meta.sheets.length > 1) {
      sheetSelect = el('select', { id: 'qtoSheet' + entry.id, 'aria-label': 'Sheet' });
      meta.sheets.forEach(function (s) {
        var o = el('option', { value: s, text: s }); if (s === meta.sheet) o.selected = true; sheetSelect.appendChild(o);
      });
      controls.appendChild(el('label', { for: 'qtoSheet' + entry.id, text: 'Sheet ' }, [sheetSelect]));
    }
    var headerInput = el('input', { type: 'number', min: '1', max: '3000', id: 'qtoHdr' + entry.id, value: meta.header_row || '', placeholder: String(meta.default_header_row), 'aria-label': 'Dòng tiêu đề' });
    controls.appendChild(el('label', { for: 'qtoHdr' + entry.id, text: 'Dòng tiêu đề (mặc định ' + meta.default_header_row + ') ' }, [headerInput]));
    panel.appendChild(controls);

    var selects = {};
    var grid = el('div', { class: 'qto-map-grid' });
    if (!meta.needs_header) {
      FIELDS.forEach(function (f) {
        var select = el('select', { id: 'qtoMap' + entry.id + f.key });
        select.appendChild(el('option', { value: '', text: '— Không có, tôi nhập tay —' }));
        meta.columns.forEach(function (c) {
          var o = el('option', { value: String(c.index), text: c.letter + ' · ' + c.title });
          if (meta.mapping[f.key] === c.index) o.selected = true;
          select.appendChild(o);
        });
        selects[f.key] = select;
        var label = el('label', { for: select.id, class: f.required && meta.mapping[f.key] == null ? 'missing' : '' });
        label.appendChild(document.createTextNode(f.label + (f.required ? ' (*)' : '')));
        if (meta.auto_mapping[f.key] != null && meta.auto_mapping[f.key] === meta.mapping[f.key]) {
          label.appendChild(el('small', { class: 'muted', text: ' tự nhận diện' }));
        }
        grid.appendChild(el('div', { class: 'qto-map-item' }, [label, select]));
      });
      panel.appendChild(grid);
      panel.appendChild(el('p', { class: 'muted qto-note', text: 'Cột nào của báo giá không có trong danh sách trên (Phụ lục, Thời gian đặt hàng…) sẽ được bỏ qua. “Thành tiền” luôn tự tính = Số lượng × Đơn giá.' }));
    }
    var apply = el('button', { type: 'button', text: 'Áp dụng' });
    apply.addEventListener('click', function () {
      if (entry.edited && !window.confirm('Các chỉnh sửa trên dòng của file này sẽ mất khi đọc lại. Tiếp tục?')) return;
      var mapping = null;
      if (!meta.needs_header) {
        mapping = {};
        FIELDS.forEach(function (f) { mapping[f.key] = selects[f.key].value === '' ? null : Number(selects[f.key].value); });
      }
      var sheet = sheetSelect ? sheetSelect.value : meta.sheet;
      var headerRow = Number(headerInput.value) || null;
      var sheetChanged = sheet !== meta.sheet || headerRow !== meta.header_row;
      reparse(entry, { mapping: sheetChanged ? null : mapping, headerRow: headerRow, sheet: sheet }).then(function (ok) {
        if (ok) {
          if (!sheetChanged && mapping) { entry.remembered = false; rememberMapping(entry.meta); }
          entry.open = needsAttention(entry.meta);
        }
        refresh();
      });
    });
    panel.appendChild(el('div', { class: 'qto-map-actions' }, [apply]));
    return panel;
  }

  function removeFile(entry) {
    if (entry.edited && !window.confirm('Bỏ file này sẽ mất các chỉnh sửa trên dòng của nó. Tiếp tục?')) return;
    if (entry.notice) entry.notice.remove();
    state.files = state.files.filter(function (f) { return f !== entry; });
    state.rows = state.rows.filter(function (r) { return r.fileId !== entry.id; });
    refresh();
  }

  /* ---------- kiểm tra dòng ---------- */
  function rowIssues(row) {
    var issues = [];
    FIELDS.forEach(function (f) {
      var raw = row.raw[f.key].trim();
      if (f.kind === 'num') {
        var n = parseNum(raw);
        if (raw !== '' && (isNaN(n) || n < 0)) { issues.push({ key: f.key, text: f.label + ' không phải số hợp lệ' }); return; }
        if (f.key === 'qty' && n != null && n <= 0) { issues.push({ key: f.key, text: 'Số lượng phải lớn hơn 0' }); return; }
        if (f.required && n == null) issues.push({ key: f.key, text: 'Thiếu ' + f.label });
      } else if (f.kind === 'choice') {
        if (raw === '') { if (f.required) issues.push({ key: f.key, text: 'Thiếu ' + f.label }); }
        else if (CONFIG.typeChoices.indexOf(raw) < 0) issues.push({ key: f.key, text: f.label + ' không hợp lệ' });
      } else if (f.required && raw === '') {
        issues.push({ key: f.key, text: 'Thiếu ' + f.label });
      }
    });
    return issues;
  }

  function rowAmount(row) {
    var q = parseNum(row.raw.qty), p = parseNum(row.raw.price);
    return (q == null || p == null || isNaN(q) || isNaN(p)) ? null : q * p;
  }

  function duplicateCodes() {
    var seen = {}, dupes = {};
    state.rows.forEach(function (r) {
      var c = r.raw.code.trim().toLowerCase();
      if (!c) return;
      seen[c] = (seen[c] || 0) + 1;
      if (seen[c] > 1) dupes[c] = true;
    });
    return dupes;
  }

  /* ---------- bảng ---------- */
  function buildHead() {
    els.head.textContent = '';
    var tr = el('tr');
    tr.appendChild(el('th', { class: 'qto-c-check', scope: 'col', text: 'Chọn' }));
    tr.appendChild(el('th', { class: 'qto-c-idx', scope: 'col', text: '#' }));
    tr.appendChild(el('th', { class: 'qto-c-src', scope: 'col', text: 'Báo giá nguồn' }));
    COLUMNS.forEach(function (c) {
      var f = FIELD_BY_KEY[c[0]];
      tr.appendChild(el('th', { scope: 'col', class: 'qto-col-' + c[0], text: c[1] + (f && f.required ? ' (*)' : '') }));
    });
    tr.appendChild(el('th', { scope: 'col', class: 'qto-c-status', text: 'Trạng thái' }));
    tr.appendChild(el('th', { scope: 'col', class: 'qto-c-del', text: '' }));
    els.head.appendChild(tr);
  }

  function renderTable() {
    els.body.textContent = '';
    state.rows.forEach(function (row, i) { els.body.appendChild(renderRow(row, i)); });
    applyFilter();
    updateRowStates();
  }

  function renderRow(row, index) {
    var tr = el('tr', { 'data-row': row.id });
    var check = el('input', { type: 'checkbox', 'aria-label': 'Chọn dòng ' + (index + 1) });
    check.checked = row.selected;
    check.addEventListener('change', function () { row.selected = check.checked; updateRowStates(); });
    tr.appendChild(el('td', { class: 'qto-c-check' }, [check]));
    tr.appendChild(el('td', { class: 'qto-c-idx', text: String(index + 1) }));
    tr.appendChild(el('td', { class: 'qto-c-src', title: row.sourceName + (row.sourceRow ? ' · dòng ' + row.sourceRow : ''),
      text: row.sourceName }));
    COLUMNS.forEach(function (c) {
      var key = c[0];
      var td = el('td', { class: 'qto-cell qto-col-' + key, 'data-col': key });
      if (key === 'amount') {
        td.appendChild(el('span', { class: 'qto-amount' }));
      } else if (key === 'type') {
        var select = el('select', { 'data-key': key, 'aria-label': 'Loại hàng dòng ' + (index + 1) });
        select.appendChild(el('option', { value: '', text: '— chọn —' }));
        CONFIG.typeChoices.forEach(function (t) { select.appendChild(el('option', { value: t, text: t })); });
        select.value = row.raw.type;
        select.addEventListener('change', function () { onEdit(row, key, select.value, tr); });
        td.appendChild(select);
      } else {
        var isNum = NUM_KEYS.indexOf(key) >= 0;
        var input = el('input', { type: 'text', 'data-key': key, autocomplete: 'off', inputmode: isNum ? 'decimal' : null,
          'aria-label': c[1] + ' dòng ' + (index + 1) });
        input.value = isNum ? displayNum(row.raw[key]) : row.raw[key];
        if (isNum) {
          input.addEventListener('focus', function () { input.value = row.raw[key]; });
          input.addEventListener('blur', function () { input.value = displayNum(row.raw[key]); });
        }
        input.addEventListener('input', function () { onEdit(row, key, input.value, tr); });
        input.addEventListener('keydown', onCellKey);
        td.appendChild(input);
      }
      tr.appendChild(td);
    });
    tr.appendChild(el('td', { class: 'qto-c-status' }));
    var del = el('button', { type: 'button', class: 'secondary qto-del', 'aria-label': 'Xóa dòng ' + (index + 1), title: 'Xóa dòng', text: '✕' });
    del.addEventListener('click', function () { deleteRow(row); });
    tr.appendChild(el('td', { class: 'qto-c-del' }, [del]));
    return tr;
  }

  function displayNum(raw) {
    var n = parseNum(raw);
    return (n == null || isNaN(n)) ? raw : fmtNum(n);
  }

  function onEdit(row, key, value, tr) {
    row.raw[key] = value;
    var entry = state.files.filter(function (f) { return f.id === row.fileId; })[0];
    if (entry) entry.edited = true;
    if (key === 'qty' && value.trim() !== '' && !row.selected) {      // sửa số lượng => tự tick dòng
      row.selected = true;
      tr.querySelector('.qto-c-check input').checked = true;
    }
    updateRowStates();
  }

  function onCellKey(ev) {
    var move = (ev.key === 'Enter' && !ev.shiftKey) || ev.key === 'ArrowDown' ? 1 : ((ev.key === 'Enter' && ev.shiftKey) || ev.key === 'ArrowUp' ? -1 : 0);
    if (!move) return;
    ev.preventDefault();
    var tr = ev.target.closest('tr');
    var next = tr;
    do { next = move > 0 ? next.nextElementSibling : next.previousElementSibling; } while (next && next.hidden);
    if (!next) return;
    var target = next.querySelector('[data-key="' + ev.target.getAttribute('data-key') + '"]');
    if (target) { target.focus(); if (target.select) target.select(); }
  }

  function deleteRow(row) {
    var hasData = FIELDS.some(function (f) { return row.raw[f.key].trim() !== ''; });
    if (hasData && !window.confirm('Xóa hẳn dòng này khỏi danh sách?')) return;
    state.rows = state.rows.filter(function (r) { return r !== row; });
    refresh();
  }

  function applyFilter() {
    var q = els.filter.value.trim().toLowerCase();
    Array.prototype.forEach.call(els.body.children, function (tr, i) {
      var row = state.rows[i];
      var hay = [row.raw.name, row.raw.code, row.raw.brand, row.sourceName].join(' ').toLowerCase();
      tr.hidden = q !== '' && hay.indexOf(q) < 0;
    });
  }

  /* Cập nhật trạng thái/tổng mà không dựng lại bảng (giữ con trỏ khi đang gõ). */
  function updateRowStates() {
    var dupes = duplicateCodes();
    var selected = 0, blocked = 0, total = 0, firstBad = null;
    Array.prototype.forEach.call(els.body.children, function (tr, i) {
      var row = state.rows[i];
      if (!row) return;
      var issues = rowIssues(row);
      var bad = {};
      issues.forEach(function (it) { bad[it.key] = true; });
      tr.classList.toggle('is-selected', row.selected);
      tr.classList.toggle('is-invalid', row.selected && issues.length > 0);
      Array.prototype.forEach.call(tr.querySelectorAll('.qto-cell'), function (td) {
        td.classList.toggle('is-missing', !!bad[td.getAttribute('data-col')]);
      });
      var amount = rowAmount(row);
      tr.querySelector('.qto-amount').textContent = amount == null ? '' : fmtNum(amount);
      var minInput = tr.querySelector('input[data-key="min_price"]');   // để trống => khi xuất bằng Đơn giá
      var priceNum = parseNum(row.raw.price);
      if (minInput) minInput.placeholder = (priceNum == null || isNaN(priceNum)) ? '' : fmtNum(priceNum) + ' (= Đơn giá)';
      var status = tr.querySelector('.qto-c-status');
      status.textContent = '';
      var code = row.raw.code.trim().toLowerCase();
      if (issues.length) status.appendChild(el('span', { class: 'qto-flag ' + (row.selected ? 'bad' : 'soft'), text: issues.map(function (it) { return it.text; }).join('; ') }));
      if (code && dupes[code]) status.appendChild(el('span', { class: 'qto-flag warn', text: 'Trùng Code' }));
      if (row.selected) {
        selected++;
        if (issues.length) { blocked++; if (!firstBad) firstBad = tr; }
        else if (amount != null) total += amount;
      }
    });
    els.summary.textContent = '';
    els.summary.appendChild(el('span', { text: 'Đã chọn ' + selected + '/' + state.rows.length + ' dòng' }));
    els.summary.appendChild(el('strong', { text: 'Tạm tính: ' + fmtMoney(total) }));
    var reason = '';
    if (!state.rows.length) reason = '';
    else if (!selected) reason = 'Chưa chọn dòng hàng nào.';
    else if (blocked) reason = blocked + ' dòng đã chọn còn thiếu hoặc sai thông tin (tô đỏ). Hãy sửa hoặc bỏ chọn để tải file.';
    els.blockers.textContent = '';
    if (reason) {
      els.blockers.appendChild(document.createTextNode(reason + ' '));
      if (firstBad) {
        var jump = el('button', { type: 'button', class: 'secondary', text: 'Đến dòng lỗi đầu tiên' });
        jump.addEventListener('click', function () { firstBad.scrollIntoView({ block: 'center' }); });
        els.blockers.appendChild(jump);
      }
    }
    els.download.disabled = !!reason || !state.rows.length;
  }

  function refresh() {
    renderFiles();
    els.listCard.hidden = !state.files.length && !state.rows.length;
    buildHead();
    renderTable();
  }

  /* ---------- tải file đơn hàng ---------- */
  function exportRows() {
    var selected = state.rows.filter(function (r) { return r.selected; });
    var payload = selected.map(function (r) {
      var out = {};
      FIELDS.forEach(function (f) {
        var raw = r.raw[f.key].trim();
        out[f.key] = f.kind === 'num' ? parseNum(raw) : raw;
      });
      return out;
    });
    els.download.disabled = true;
    fetch(CONFIG.urls.export, { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF }, body: JSON.stringify({ rows: payload }) })
      .then(function (resp) {
        if (resp.ok) {
          var disposition = resp.headers.get('Content-Disposition') || '';
          var match = /filename="?([^";]+)"?/.exec(disposition);
          return resp.blob().then(function (blob) { saveBlob(blob, match ? match[1] : 'bang-hang-hoa.xlsx'); say('Đã tạo file đơn hàng với ' + payload.length + ' dòng.', 'ok'); });
        }
        return readJson(resp).then(function (data) {
          var detail = '';
          if (data.rows && data.rows.length) {
            detail = ' ' + data.rows.slice(0, 5).map(function (e) {
              var row = selected[e.index];
              return 'Dòng ' + (state.rows.indexOf(row) + 1) + ': ' + e.missing.concat(e.invalid).join(', ');
            }).join(' | ');
          }
          say((data.error || 'Không tạo được file đơn hàng.') + detail, 'error');
        });
      })
      .catch(function () { say('Không gửi được yêu cầu. Kiểm tra kết nối rồi thử lại.', 'error'); })
      .then(updateRowStates);
  }

  function saveBlob(blob, name) {
    var url = URL.createObjectURL(blob);
    var a = el('a', { href: url, download: name });
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
  }

  /* ---------- sự kiện ---------- */
  els.file.addEventListener('change', function () { if (els.file.files.length) addFiles(els.file.files); els.file.value = ''; });
  ['dragenter', 'dragover'].forEach(function (t) {
    els.drop.addEventListener(t, function (ev) { ev.preventDefault(); els.drop.classList.add('is-dragover'); });
  });
  ['dragleave', 'drop'].forEach(function (t) {
    els.drop.addEventListener(t, function (ev) { ev.preventDefault(); els.drop.classList.remove('is-dragover'); });
  });
  els.drop.addEventListener('drop', function (ev) { if (ev.dataTransfer && ev.dataTransfer.files.length) addFiles(ev.dataTransfer.files); });
  els.filter.addEventListener('input', applyFilter);
  $('qtoSelectVisible').addEventListener('click', function () {
    Array.prototype.forEach.call(els.body.children, function (tr, i) {
      if (!tr.hidden) { state.rows[i].selected = true; tr.querySelector('.qto-c-check input').checked = true; }
    });
    updateRowStates();
  });
  $('qtoSelectNone').addEventListener('click', function () {
    state.rows.forEach(function (r) { r.selected = false; });
    Array.prototype.forEach.call(els.body.querySelectorAll('.qto-c-check input'), function (c) { c.checked = false; });
    updateRowStates();
  });
  $('qtoAddRow').addEventListener('click', function () {
    if (state.rows.length >= CONFIG.limits.maxRows) { say('Tối đa ' + CONFIG.limits.maxRows + ' dòng.', 'error'); return; }
    var row = newRow(null, null);
    row.selected = true;
    state.rows.push(row);
    refresh();
    var last = els.body.lastElementChild;
    if (last) { last.scrollIntoView({ block: 'center' }); var first = last.querySelector('input[data-key="name"]'); if (first) first.focus(); }
  });
  els.download.addEventListener('click', function () { if (!els.download.disabled) exportRows(); });

  refresh();
})();
