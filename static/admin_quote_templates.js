(function () {
  const MAX_XLSX_BYTES = 10 * 1024 * 1024;
  const API_LIST = '/api/admin/quote-templates';
  const API_UPLOAD = '/api/admin/quote-templates';
  const API_ACTIVATE_PREFIX = '/api/admin/quote-templates/';
  const API_DOWNLOAD_PREFIX = '/api/admin/quote-templates/';
  const API_ARCHIVE_PREFIX = '/api/admin/quote-templates/';
  const API_INSPECT = '/api/admin/quote-templates/inspect';
  const API_CONTEXTS = '/api/admin/quote-template-contexts';
  const API_ASSIGN = '/api/admin/quote-template-assignments';

  const els = {
    pageAlert: document.getElementById('qtPageAlert'),
    activeBadge: document.getElementById('qtActiveBadge'),
    emptyActive: document.getElementById('qtEmptyActive'),
    activeMeta: document.getElementById('qtActiveMeta'),
    activeFilename: document.getElementById('qtActiveFilename'),
    activeProfile: document.getElementById('qtActiveProfile'),
    activeSize: document.getElementById('qtActiveSize'),
    activeCreated: document.getElementById('qtActiveCreated'),
    activeActivated: document.getElementById('qtActiveActivated'),
    activeUploader: document.getElementById('qtActiveUploader'),
    uploadForm: document.getElementById('qtUploadForm'),
    csrfToken: document.getElementById('qtCsrfToken'),
    workbook: document.getElementById('qtWorkbook'),
    activate: document.getElementById('qtActivate'),
    uploadBtn: document.getElementById('qtUploadBtn'),
    uploadStatus: document.getElementById('qtUploadStatus'),
    inspectBtn: document.getElementById('qtInspectBtn'),
    sheet: document.getElementById('qtSheet'),
    headerRow: document.getElementById('qtHeaderRow'),
    dataStartRow: document.getElementById('qtDataStartRow'),
    totalLabel: document.getElementById('qtTotalLabel'),
    mappingBody: document.getElementById('qtMappingBody'),
    mappingState: document.getElementById('qtMappingState'),
    mappingBadge: document.getElementById('qtMappingBadge'),
    assignments: document.getElementById('qtAssignments'),
    historyBody: document.getElementById('qtHistoryBody'),
    activateDialog: document.getElementById('qtActivateDialog'),
    activateDialogText: document.getElementById('qtActivateDialogText'),
    activateCancel: document.getElementById('qtActivateCancel'),
    activateConfirm: document.getElementById('qtActivateConfirm'),
    archiveDialog: document.getElementById('qtArchiveDialog'),
    archiveDialogText: document.getElementById('qtArchiveDialogText'),
    archiveCancel: document.getElementById('qtArchiveCancel'),
    archiveConfirm: document.getElementById('qtArchiveConfirm'),
  };

  let templates = [];
  let uploadInProgress = false;
  let activateInProgress = false;
  let pendingActivateId = null;
  let archiveInProgress = false;
  let pendingArchiveId = null;
  let preview = null;
  let teams = [];

  function setText(el, value) {
    if (el) el.textContent = value == null || value === '' ? '-' : String(value);
  }

  function setAlert(message, kind) {
    if (!els.pageAlert) return;
    els.pageAlert.className = 'alert';
    setText(els.pageAlert, '');
    if (!message) return;
    els.pageAlert.classList.add(kind === 'ok' ? 'ok' : 'err');
    setText(els.pageAlert, message);
  }

  function setUploadStatus(message, kind) {
    if (!els.uploadStatus) return;
    els.uploadStatus.className = 'upload-status';
    if (kind) els.uploadStatus.classList.add(kind);
    setText(els.uploadStatus, message);
  }

  function statusMessage(status, fallback) {
    if (status === 401) return 'Chưa đăng nhập. Vui lòng đăng nhập lại.';
    if (status === 403) return 'Không có quyền quản trị để thao tác mẫu báo giá.';
    if (status === 400) return fallback || 'Dữ liệu gửi lên không hợp lệ.';
    if (status === 413) return 'File quá lớn. Vui lòng chọn file .xlsx tối đa 10 MB.';
    if (status === 409) return fallback || 'Không thể hoàn tất do trạng thái mẫu vừa thay đổi.';
    if (status >= 500) return 'Hệ thống đang lỗi. Vui lòng thử lại sau.';
    return fallback || 'Thao tác thất bại.';
  }

  async function parseJsonResponse(response) {
    const data = await response.json().catch(() => ({}));
    if (response.ok) return data;
    const fallback = data.error || data.message || '';
    throw new Error(statusMessage(response.status, fallback));
  }

  function formatBytes(value) {
    const size = Number(value || 0);
    if (!Number.isFinite(size) || size <= 0) return '-';
    if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(2)} MB`;
    return `${Math.max(1, Math.round(size / 1024))} KB`;
  }

  function formatDate(value) {
    if (!value) return '-';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString('vi-VN', {
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  function clearNode(node) {
    if (!node) return;
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function icon(className) {
    const el = document.createElement('i');
    el.className = className;
    el.setAttribute('aria-hidden', 'true');
    return el;
  }

  function renderActive() {
    const active = templates.find((item) => item.is_active && !item.archived_at);
    if (!active) {
      if (els.emptyActive) els.emptyActive.hidden = false;
      if (els.activeMeta) els.activeMeta.hidden = true;
      if (els.activeBadge) {
        els.activeBadge.className = 'badge inactive';
        setText(els.activeBadge, 'Chưa có mẫu');
      }
      return;
    }

    if (els.emptyActive) els.emptyActive.hidden = true;
    if (els.activeMeta) els.activeMeta.hidden = false;
    if (els.activeBadge) {
      els.activeBadge.className = 'badge active';
      setText(els.activeBadge, 'Mẫu mặc định toàn hệ thống');
    }
    setText(els.activeFilename, active.filename);
    setText(els.activeProfile, active.profile_version);
    setText(els.activeSize, formatBytes(active.content_size));
    setText(els.activeCreated, formatDate(active.created_at));
    setText(els.activeActivated, formatDate(active.activated_at));
    setText(els.activeUploader, active.uploaded_by);
  }

  function appendCell(row, text, className, title) {
    const cell = document.createElement('td');
    if (className) cell.className = className;
    if (title) cell.title = title;
    setText(cell, text);
    row.appendChild(cell);
    return cell;
  }

  function appendStatusCell(row, item) {
    const cell = document.createElement('td');
    const badge = document.createElement('span');
    badge.className = item.archived_at ? 'badge archived' : (item.is_active ? 'badge active' : 'badge inactive');
    setText(badge, item.archived_at ? 'Đã lưu trữ' : (item.is_active ? 'Mẫu mặc định toàn hệ thống' : 'Sẵn sàng gán cho team'));
    cell.appendChild(badge);
    row.appendChild(cell);
  }

  function buildDownloadLink(item) {
    const link = document.createElement('a');
    link.className = 'btn icon-btn';
    link.href = `${API_DOWNLOAD_PREFIX}${encodeURIComponent(item.id)}/download`;
    link.title = `Tải lại ${item.filename || 'mẫu báo giá'} để đối soát`;
    link.setAttribute('aria-label', link.title);
    link.appendChild(icon('fas fa-download'));
    return link;
  }

  function buildActivateButton(item) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn';
    button.title = `Đặt phiên bản ${item.id} làm mẫu mặc định`;
    button.appendChild(icon('fas fa-check'));
    const label = document.createElement('span');
    setText(label, 'Đặt làm mẫu mặc định');
    button.appendChild(label);
    button.addEventListener('click', () => openActivateDialog(item));
    return button;
  }

  function buildArchiveButton(item) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn';
    button.title = `Lưu trữ phiên bản ${item.id}`;
    button.appendChild(icon('fas fa-archive'));
    const label = document.createElement('span');
    setText(label, 'Lưu trữ');
    button.appendChild(label);
    button.addEventListener('click', () => openArchiveDialog(item));
    return button;
  }

  function appendUsageCell(row, item) {
    const usage = Array.isArray(item.team_usage) ? item.team_usage : [];
    appendCell(row, usage.length ? usage.map((team) => team.name).join(', ') : 'Chưa có team');
  }

  function renderHistory() {
    clearNode(els.historyBody);
    if (!els.historyBody) return;
    if (!templates.length) {
      const row = document.createElement('tr');
      appendCell(row, 'Chưa có phiên bản nào.', '');
      row.firstChild.colSpan = 9;
      els.historyBody.appendChild(row);
      return;
    }
    templates.forEach((item) => {
      const row = document.createElement('tr');
      appendCell(row, `#${item.id}`);
      appendCell(row, item.filename, 'filename-cell', item.filename);
      appendCell(row, item.profile_version);
      appendCell(row, formatBytes(item.content_size));
      appendCell(row, item.uploaded_by);
      appendCell(row, formatDate(item.created_at));
      appendStatusCell(row, item);
      appendUsageCell(row, item);

      const actionsCell = document.createElement('td');
      const actions = document.createElement('div');
      actions.className = 'actions';
      actions.appendChild(buildDownloadLink(item));
      if (!item.is_active && !item.archived_at) {
        actions.appendChild(buildActivateButton(item));
      }
      if (!item.is_active && !item.archived_at && !(item.team_usage || []).length) {
        actions.appendChild(buildArchiveButton(item));
      }
      actionsCell.appendChild(actions);
      row.appendChild(actionsCell);
      els.historyBody.appendChild(row);
    });
  }

  function renderAll() {
    renderActive();
    renderHistory();
  }

  async function loadTemplates() {
    try {
      const response = await fetch(API_LIST, { credentials: 'same-origin' });
      const data = await parseJsonResponse(response);
      templates = Array.isArray(data.templates) ? data.templates : [];
      renderAll();
    } catch (err) {
      setAlert(err.message || 'Không tải được danh sách mẫu báo giá.', 'err');
      templates = [];
      renderAll();
    }
  }

  function selectedFile() {
    return els.workbook && els.workbook.files ? els.workbook.files[0] : null;
  }

  function validateSelectedFile(file) {
    if (!file) return 'Vui lòng chọn file .xlsx.';
    const name = file.name || '';
    const lower = name.toLowerCase();
    if (!lower.endsWith('.xlsx') || lower.endsWith('.xlsm') || lower.endsWith('.xls')) {
      return 'Chỉ hỗ trợ file .xlsx, không hỗ trợ .xls/.xlsm.';
    }
    if (file.size > MAX_XLSX_BYTES) {
      return 'File quá lớn. Giới hạn tối đa là 10 MB.';
    }
    return '';
  }

  function updateSelectedFileStatus() {
    preview = null;
    if (els.uploadBtn) els.uploadBtn.disabled = true;
    if (els.mappingBody) clearNode(els.mappingBody);
    setText(els.mappingBadge, 'Chưa xem trước');
    const file = selectedFile();
    if (!file) {
      setUploadStatus('Chưa chọn file.', '');
      return;
    }
    const error = validateSelectedFile(file);
    if (error) {
      setUploadStatus(error, 'err');
      return;
    }
    setUploadStatus(`Đã chọn: ${file.name} (${formatBytes(file.size)}).`, 'ok');
  }

  function normalized(value) {
    return String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
  }

  const FIELD_HINTS = {
    sequence: ['stt', 'so thu tu'], Name: ['ten hang', 'name'], Code: ['code', 'ma hang'],
    Cas: ['cas'], Brand: ['hang', 'brand'], Size: ['don vi tinh', 'on vi tinh', 'quy cach', 'size'],
    Note: ['ghi chu hang hoa', 'note'], Compliance_Combined: ['ghi chu khac', 'compliance'],
    Unit_Price_Value: ['gia nhap chua vat', 'don gia', 'unit price'],
  };

  function suggestedColumn(field, headers, used) {
    const hints = FIELD_HINTS[field] || [];
    let hit = headers.find((item) => !used.has(item.column) && hints.some((hint) => normalized(item.header).includes(hint)));
    if (!hit && field === 'sequence') hit = headers.find((item) => item.column === 'A' && !used.has(item.column));
    if (hit) used.add(hit.column);
    return hit ? hit.column : '';
  }

  function mappingValue() {
    if (!preview) return null;
    const mapping = {};
    els.mappingBody.querySelectorAll('select[data-field]').forEach((select) => {
      if (select.value) mapping[select.dataset.field] = select.value;
    });
    return { profile_version: 'BG_V1', mapping_version: 1, sheet: els.sheet.value,
      header_row: Number(els.headerRow.value), product_start_row: Number(els.dataStartRow.value),
      total_label: els.totalLabel.value.trim(), mapping };
  }

  function validateMappingUI() {
    const value = mappingValue();
    if (!value) return false;
    const selected = Object.values(value.mapping);
    const required = preview.fields.filter((field) => field.required && !value.mapping[field.key]);
    const duplicate = new Set(selected).size !== selected.length;
    const valid = !required.length && !duplicate && value.product_start_row > value.header_row && Boolean(value.total_label);
    els.mappingState.className = `mapping-state${valid ? '' : ' err'}`;
    setText(els.mappingState, valid ? 'Ánh xạ hợp lệ để lưu; hệ thống sẽ kiểm tra vùng tổng và công thức trong cùng giao dịch.' :
      required.length ? `Thiếu trường bắt buộc: ${required.map((item) => item.label).join(', ')}.` :
      duplicate ? 'Một cột đang được ánh xạ nhiều lần.' : 'Kiểm tra lại vùng dữ liệu và nhãn tổng.');
    setText(els.mappingBadge, valid ? 'Sẵn sàng' : 'Cần chỉnh');
    els.mappingBadge.className = `badge ${valid ? 'active' : 'warn'}`;
    if (els.uploadBtn) els.uploadBtn.disabled = !valid;
    return valid;
  }

  function renderMapping(data) {
    preview = data;
    clearNode(els.mappingBody);
    clearNode(els.sheet);
    data.sheets.forEach((name) => {
      const option = document.createElement('option'); option.value = name; option.textContent = name;
      option.selected = name === data.sheet; els.sheet.appendChild(option);
    });
    els.headerRow.value = data.header_row;
    els.dataStartRow.value = data.product_start_row;
    const used = new Set();
    data.fields.forEach((field) => {
      const row = document.createElement('tr');
      const label = document.createElement('td'); label.textContent = field.label;
      if (field.required) { const mark = document.createElement('span'); mark.className = 'required-mark'; mark.textContent = ' *'; label.appendChild(mark); }
      const cell = document.createElement('td'); const select = document.createElement('select');
      select.dataset.field = field.key;
      const empty = document.createElement('option'); empty.value = ''; empty.textContent = field.required ? 'Chọn cột…' : 'Không ánh xạ'; select.appendChild(empty);
      const suggested = suggestedColumn(field.key, data.headers, used);
      data.headers.forEach((header) => { const option = document.createElement('option'); option.value = header.column;
        option.textContent = `${header.column} — ${header.header}`; option.selected = header.column === suggested; select.appendChild(option); });
      select.addEventListener('change', validateMappingUI); cell.appendChild(select); row.append(label, cell); els.mappingBody.appendChild(row);
    });
    validateMappingUI();
  }

  async function inspectTemplate() {
    const file = selectedFile(); const error = validateSelectedFile(file);
    if (error) { setUploadStatus(error, 'err'); return; }
    els.inspectBtn.disabled = true; setUploadStatus('Đang đọc trang tính và hàng tiêu đề…', 'loading');
    try {
      const body = new FormData(); body.append('workbook', file); body.append('csrf_token', els.csrfToken.value);
      body.append('sheet', els.sheet.value || 'BG'); body.append('header_row', els.headerRow.value || '16');
      const response = await fetch(API_INSPECT, { method: 'POST', body, credentials: 'same-origin' });
      const data = await parseJsonResponse(response); renderMapping(data.preview); setUploadStatus('Đã đọc tệp. Kiểm tra ánh xạ trước khi lưu.', 'ok');
    } catch (err) { preview = null; setUploadStatus(err.message || 'Không xem trước được tệp.', 'err'); }
    finally { els.inspectBtn.disabled = false; }
  }

  async function uploadTemplate(event) {
    event.preventDefault();
    if (uploadInProgress) return;
    const file = selectedFile();
    const error = validateSelectedFile(file);
    if (error) {
      setUploadStatus(error, 'err');
      return;
    }
    if (!validateMappingUI()) { setUploadStatus('Cần xem trước và hoàn tất ánh xạ trước khi lưu.', 'err'); return; }

    uploadInProgress = true;
    if (els.uploadBtn) els.uploadBtn.disabled = true;
    if (els.workbook) els.workbook.disabled = true;
    if (els.activate) els.activate.disabled = true;
    setUploadStatus('Đang tải lên và kiểm tra tệp...', 'loading');
    setAlert('', '');

    try {
      const body = new FormData();
      body.append('workbook', file);
      body.append('activate', els.activate && els.activate.checked ? 'true' : 'false');
      body.append('mapping', JSON.stringify(mappingValue()));
      body.append('csrf_token', els.csrfToken ? els.csrfToken.value : '');
      const response = await fetch(API_UPLOAD, {
        method: 'POST',
        body,
        credentials: 'same-origin',
      });
      await parseJsonResponse(response);
      if (els.workbook) els.workbook.value = '';
      if (els.activate) els.activate.checked = true;
      preview = null;
      clearNode(els.mappingBody);
      setText(els.mappingBadge, 'Chưa xem trước');
      setUploadStatus('Tải lên thành công. Đã làm mới lịch sử phiên bản.', 'ok');
      setAlert('Đã tải lên mẫu báo giá.', 'ok');
      await loadTemplates();
      await loadContexts();
    } catch (err) {
      setUploadStatus(err.message || 'Tải lên thất bại.', 'err');
    } finally {
      uploadInProgress = false;
      if (els.uploadBtn) els.uploadBtn.disabled = !preview;
      if (els.workbook) els.workbook.disabled = false;
      if (els.activate) els.activate.disabled = false;
    }
  }

  function renderAssignments() {
    clearNode(els.assignments);
    teams.filter((team) => team.status === 'ACTIVE').forEach((team) => {
      const row = document.createElement('div'); row.className = 'assignment-row';
      const name = document.createElement('strong'); name.textContent = team.name;
      const select = document.createElement('select'); select.appendChild(new Option('Dùng mẫu mặc định', ''));
      templates.filter((item) => !item.archived_at).forEach((item) => select.appendChild(new Option(
        `#${item.id} · ${item.filename}${item.is_active ? ' · Mẫu mặc định toàn hệ thống' : ''}`,
        item.id,
      )));
      select.value = team.template_id == null ? '' : String(team.template_id);
      const save = document.createElement('button'); save.type = 'button'; save.className = 'btn'; save.textContent = 'Lưu gán';
      save.addEventListener('click', async () => {
        save.disabled = true;
        try { const response = await fetch(API_ASSIGN, { method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': els.csrfToken.value },
          body: JSON.stringify({ team_id: team.id, template_id: select.value || null }) });
          await parseJsonResponse(response); setAlert(`Đã cập nhật mẫu cho team ${team.name}.`, 'ok'); await loadContexts();
        } catch (err) { setAlert(err.message, 'err'); } finally { save.disabled = false; }
      });
      row.append(name, select, save); els.assignments.appendChild(row);
    });
  }

  async function loadContexts() {
    try { const response = await fetch(API_CONTEXTS, { credentials: 'same-origin' }); const data = await parseJsonResponse(response);
      teams = data.teams || []; templates = data.templates || templates; renderAll(); renderAssignments();
    } catch (err) { setText(els.assignments, err.message || 'Không tải được thông tin gán mẫu.'); }
  }

  function openActivateDialog(item) {
    pendingActivateId = item.id;
    setText(els.activateDialogText, `Đặt phiên bản #${item.id} (${item.filename}) làm mẫu mặc định toàn hệ thống?`);
    if (els.activateDialog && typeof els.activateDialog.showModal === 'function') {
      els.activateDialog.showModal();
    }
  }

  function closeActivateDialog() {
    pendingActivateId = null;
    if (els.activateDialog && els.activateDialog.open) {
      els.activateDialog.close();
    }
  }

  async function activateTemplate() {
    if (!pendingActivateId || activateInProgress) return;
    activateInProgress = true;
    if (els.activateConfirm) els.activateConfirm.disabled = true;
    setAlert('', '');
    try {
      const response = await fetch(`${API_ACTIVATE_PREFIX}${encodeURIComponent(pendingActivateId)}/activate`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRF-Token': els.csrfToken ? els.csrfToken.value : '' },
      });
      await parseJsonResponse(response);
      closeActivateDialog();
      setAlert('Đã đặt phiên bản làm mẫu mặc định toàn hệ thống.', 'ok');
      await loadContexts();
    } catch (err) {
      setAlert(err.message || 'Không thể đặt mẫu mặc định.', 'err');
    } finally {
      activateInProgress = false;
      if (els.activateConfirm) els.activateConfirm.disabled = false;
    }
  }

  function openArchiveDialog(item) {
    pendingArchiveId = item.id;
    setText(els.archiveDialogText, `Lưu trữ phiên bản #${item.id} (${item.filename})? Mẫu vẫn có thể tải lại để đối soát.`);
    if (els.archiveDialog && typeof els.archiveDialog.showModal === 'function') {
      els.archiveDialog.showModal();
    }
  }

  function closeArchiveDialog() {
    pendingArchiveId = null;
    if (els.archiveDialog && els.archiveDialog.open) els.archiveDialog.close();
  }

  async function archiveTemplate() {
    if (!pendingArchiveId || archiveInProgress) return;
    archiveInProgress = true;
    if (els.archiveConfirm) els.archiveConfirm.disabled = true;
    setAlert('', '');
    try {
      const response = await fetch(`${API_ARCHIVE_PREFIX}${encodeURIComponent(pendingArchiveId)}/archive`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRF-Token': els.csrfToken ? els.csrfToken.value : '' },
      });
      await parseJsonResponse(response);
      closeArchiveDialog();
      setAlert('Đã lưu trữ phiên bản mẫu báo giá.', 'ok');
      await loadContexts();
    } catch (err) {
      setAlert(err.message || 'Lưu trữ thất bại.', 'err');
    } finally {
      archiveInProgress = false;
      if (els.archiveConfirm) els.archiveConfirm.disabled = false;
    }
  }

  if (els.workbook) els.workbook.addEventListener('change', updateSelectedFileStatus);
  if (els.inspectBtn) els.inspectBtn.addEventListener('click', inspectTemplate);
  if (els.uploadForm) els.uploadForm.addEventListener('submit', uploadTemplate);
  if (els.activateCancel) els.activateCancel.addEventListener('click', closeActivateDialog);
  if (els.activateConfirm) els.activateConfirm.addEventListener('click', activateTemplate);
  if (els.archiveCancel) els.archiveCancel.addEventListener('click', closeArchiveDialog);
  if (els.archiveConfirm) els.archiveConfirm.addEventListener('click', archiveTemplate);
  if (els.activateDialog) {
    els.activateDialog.addEventListener('cancel', () => {
      pendingActivateId = null;
    });
  }
  if (els.archiveDialog) {
    els.archiveDialog.addEventListener('cancel', () => { pendingArchiveId = null; });
  }

  if (els.sheet) els.sheet.addEventListener('change', () => { preview = null; if (els.uploadBtn) els.uploadBtn.disabled = true; });
  if (els.headerRow) els.headerRow.addEventListener('change', () => { preview = null; if (els.uploadBtn) els.uploadBtn.disabled = true; });
  loadTemplates().then(loadContexts);
})();
