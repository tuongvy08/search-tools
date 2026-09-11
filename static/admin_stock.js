(() => {
  const upload = document.querySelector('[data-stock-upload]');
  if (upload) upload.addEventListener('submit', () => {
    const status = upload.querySelector('[data-upload-status]');
    if (status) status.textContent = 'Đang tải workbook; snapshot hiện tại chưa thay đổi.';
  });
  const job = document.querySelector('[data-stock-job]');
  if (!job || !['queued', 'running'].includes(job.dataset.jobStatus)) return;
  let failures = 0;
  const poll = async () => {
    if (document.hidden) { window.setTimeout(poll, 4000); return; }
    try {
      const response = await fetch(job.dataset.pollUrl, {credentials: 'same-origin', cache: 'no-store', headers: {'Accept': 'application/json'}});
      if (!response.ok || response.redirected) throw new Error('unavailable');
      const data = await response.json();
      if (data.status !== job.dataset.jobStatus) { window.location.reload(); return; }
      const count = job.querySelector('[data-progress-text]');
      if (count) count.textContent = Number(data.processed_count).toLocaleString('vi-VN');
      const progress = job.querySelector('progress');
      if (progress && data.row_count) { progress.max = data.row_count; progress.value = data.processed_count; }
      const message = job.querySelector('[data-poll-message]');
      if (message) message.textContent = data.cancel_requested ? 'Đã yêu cầu hủy; snapshot hiện tại vẫn được giữ.' : 'Worker đang xử lý nền; snapshot hiện tại vẫn phục vụ tra cứu.';
      failures = 0;
    } catch (_) {
      failures += 1;
      const message = job.querySelector('[data-poll-message]');
      if (message) message.textContent = 'Chưa lấy được tiến độ; hệ thống sẽ tự kết nối lại.';
    }
    window.setTimeout(poll, Math.min(30000, 3000 * (failures + 1)));
  };
  window.setTimeout(poll, 1800);
})();
