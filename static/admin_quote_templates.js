(function () {
  const MAX_XLSX_BYTES = 10 * 1024 * 1024;
  const API_LIST = '/api/admin/quote-templates';
  const API_UPLOAD = '/api/admin/quote-templates';
  const API_ACTIVATE_PREFIX = '/api/admin/quote-templates/';
  const API_DOWNLOAD_PREFIX = '/api/admin/quote-templates/';

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
    workbook: document.getElementById('qtWorkbook'),
    activate: document.getElementById('qtActivate'),
    uploadBtn: document.getElementById('qtUploadBtn'),
    uploadStatus: document.getElementById('qtUploadStatus'),
    historyBody: document.getElementById('qtHistoryBody'),
    activateDialog: document.getElementById('qtActivateDialog'),
    activateDialogText: document.getElementById('qtActivateDialogText'),
    activateCancel: document.getElementById('qtActivateCancel'),
    activateConfirm: document.getElementById('qtActivateConfirm'),
  };

  let templates = [];
  let uploadInProgress = false;
  let activateInProgress = false;
  let pendingActivateId = null;

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
    if (status === 403) return 'Không có quyền admin để thao tác mẫu báo giá.';
    if (status === 400) return fallback || 'Dữ liệu gửi lên không hợp lệ.';
    if (status === 413) return 'File quá lớn. Vui lòng chọn file .xlsx tối đa 10 MB.';
    if (status === 409) return fallback || 'Không thể hoàn tất do xung đột trạng thái template.';
    if (status >= 500) return 'Server đang lỗi. Vui lòng thử lại sau.';
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
    const active = templates.find((item) => item.is_active);
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
      setText(els.activeBadge, 'Đang sử dụng');
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
    badge.className = item.is_active ? 'badge active' : 'badge inactive';
    setText(badge, item.is_active ? 'Đang sử dụng' : 'Inactive');
    cell.appendChild(badge);
    row.appendChild(cell);
  }

  function buildDownloadLink(item) {
    const link = document.createElement('a');
    link.className = 'btn icon-btn';
    link.href = `${API_DOWNLOAD_PREFIX}${encodeURIComponent(item.id)}/download`;
    link.title = `Tải lại ${item.filename || 'template'}`;
    link.setAttribute('aria-label', link.title);
    link.appendChild(icon('fas fa-download'));
    return link;
  }

  function buildActivateButton(item) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn';
    button.title = `Kích hoạt phiên bản ${item.id}`;
    button.appendChild(icon('fas fa-check'));
    const label = document.createElement('span');
    setText(label, 'Kích hoạt');
    button.appendChild(label);
    button.addEventListener('click', () => openActivateDialog(item));
    return button;
  }

  function renderHistory() {
    clearNode(els.historyBody);
    if (!els.historyBody) return;
    if (!templates.length) {
      const row = document.createElement('tr');
      appendCell(row, 'Chưa có phiên bản nào.', '');
      row.firstChild.colSpan = 8;
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

      const actionsCell = document.createElement('td');
      const actions = document.createElement('div');
      actions.className = 'actions';
      actions.appendChild(buildDownloadLink(item));
      if (!item.is_active) {
        actions.appendChild(buildActivateButton(item));
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
    setAlert('', '');
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

  async function uploadTemplate(event) {
    event.preventDefault();
    if (uploadInProgress) return;
    const file = selectedFile();
    const error = validateSelectedFile(file);
    if (error) {
      setUploadStatus(error, 'err');
      return;
    }

    uploadInProgress = true;
    if (els.uploadBtn) els.uploadBtn.disabled = true;
    if (els.workbook) els.workbook.disabled = true;
    if (els.activate) els.activate.disabled = true;
    setUploadStatus('Đang upload và kiểm tra workbook...', 'loading');
    setAlert('', '');

    try {
      const body = new FormData();
      body.append('workbook', file);
      body.append('activate', els.activate && els.activate.checked ? 'true' : 'false');
      const response = await fetch(API_UPLOAD, {
        method: 'POST',
        body,
        credentials: 'same-origin',
      });
      await parseJsonResponse(response);
      if (els.workbook) els.workbook.value = '';
      if (els.activate) els.activate.checked = true;
      setUploadStatus('Upload thành công. Đã làm mới lịch sử phiên bản.', 'ok');
      setAlert('Đã upload mẫu báo giá.', 'ok');
      await loadTemplates();
    } catch (err) {
      setUploadStatus(err.message || 'Upload thất bại.', 'err');
    } finally {
      uploadInProgress = false;
      if (els.uploadBtn) els.uploadBtn.disabled = false;
      if (els.workbook) els.workbook.disabled = false;
      if (els.activate) els.activate.disabled = false;
    }
  }

  function openActivateDialog(item) {
    pendingActivateId = item.id;
    setText(els.activateDialogText, `Kích hoạt phiên bản #${item.id} (${item.filename}) thay cho mẫu đang sử dụng?`);
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
      });
      await parseJsonResponse(response);
      closeActivateDialog();
      setAlert('Đã kích hoạt phiên bản mẫu báo giá.', 'ok');
      await loadTemplates();
    } catch (err) {
      setAlert(err.message || 'Kích hoạt thất bại.', 'err');
    } finally {
      activateInProgress = false;
      if (els.activateConfirm) els.activateConfirm.disabled = false;
    }
  }

  if (els.workbook) els.workbook.addEventListener('change', updateSelectedFileStatus);
  if (els.uploadForm) els.uploadForm.addEventListener('submit', uploadTemplate);
  if (els.activateCancel) els.activateCancel.addEventListener('click', closeActivateDialog);
  if (els.activateConfirm) els.activateConfirm.addEventListener('click', activateTemplate);
  if (els.activateDialog) {
    els.activateDialog.addEventListener('cancel', () => {
      pendingActivateId = null;
    });
  }

  loadTemplates();
})();
