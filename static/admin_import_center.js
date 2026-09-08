(() => {
  const upload = document.querySelector('[data-upload]');
  if (upload) upload.addEventListener('submit', () => {
    upload.querySelector('button').disabled = true;
    upload.querySelector('[data-upload-status]').textContent = 'Đang tải workbook lên. Giữ trang mở đến khi xuất hiện tác vụ.';
  });
  const job = document.querySelector('[data-job-status]');
  if (!job || !['queued', 'running'].includes(job.dataset.jobStatus)) return;
  let failures = 0;
  const poll = async () => {
    if (document.hidden) { setTimeout(poll, 4000); return; }
    try {
      const response = await fetch(job.dataset.pollUrl, {credentials: 'same-origin', cache: 'no-store', headers: {'Accept': 'application/json'}});
      if (!response.ok || response.redirected) throw new Error('unavailable');
      const data = await response.json();
      if (data.status !== job.dataset.jobStatus) { window.location.reload(); return; }
      job.querySelector('[data-progress-text]').textContent = `${Number(data.processed_count).toLocaleString('vi-VN')} dòng đã xử lý. Trang tự cập nhật khi có kết quả.`;
      const progress = job.querySelector('progress');
      if (data.row_count) { progress.max = data.row_count; progress.value = data.processed_count; }
      job.querySelector('[data-poll-message]').textContent = data.cancel_requested ? 'Đã yêu cầu hủy; đang chờ worker hoàn tác.' : 'Đang tự cập nhật. Bạn có thể rời trang và quay lại.';
      failures = 0;
    } catch (_) {
      failures += 1;
      job.querySelector('[data-poll-message]').textContent = 'Chưa lấy được tiến độ. Hệ thống sẽ kết nối lại; tải lại trang nếu phiên đăng nhập hết hạn.';
    }
    setTimeout(poll, Math.min(30000, 3000 * (failures + 1)));
  };
  setTimeout(poll, 2000);
})();
