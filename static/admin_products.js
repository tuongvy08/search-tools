(() => {
  'use strict';
  const dialog = document.getElementById('delete-dialog');
  if (!dialog) return;
  const previewUrl = document.body.dataset.deletePreviewUrl;
  const applyUrl = document.body.dataset.deleteApplyUrl;
  const productsUrl = document.body.dataset.productsUrl;
  const csrf = document.querySelector('input[name="csrf_token"]')?.value;
  const summary = document.getElementById('delete-summary');
  const phrase = document.getElementById('delete-phrase');
  const confirmation = document.getElementById('delete-confirmation');
  const token = document.getElementById('delete-token');
  const applyButton = dialog.querySelector('[data-delete-apply]');
  const status = dialog.querySelector('.dialog-status');

  const setBusy = (button, busy, text) => {
    button.disabled = busy;
    if (text) button.textContent = text;
  };
  const messageFrom = async (response) => {
    try { return (await response.json()).message || 'Không thể xử lý yêu cầu.'; }
    catch (_) { return 'Không thể xử lý yêu cầu.'; }
  };

  document.querySelectorAll('[data-delete-preview]').forEach((button) => {
    button.addEventListener('click', async () => {
      const original = button.textContent;
      setBusy(button, true, 'Đang đếm…');
      const data = new FormData();
      data.set('csrf_token', csrf || '');
      data.set('scope_type', button.dataset.scope || '');
      if (button.dataset.productId) data.set('product_id', button.dataset.productId);
      if (button.dataset.brand) data.set('brand', button.dataset.brand);
      try {
        const response = await fetch(previewUrl, { method: 'POST', body: data, credentials: 'same-origin' });
        if (!response.ok) throw new Error(await messageFrom(response));
        const result = await response.json();
        token.value = result.token;
        phrase.textContent = result.confirmation;
        summary.textContent = result.scope_type === 'brand'
          ? `${result.count.toLocaleString('vi-VN')} sản phẩm thuộc canonical brand “${result.brand}”.`
          : `1 sản phẩm thuộc canonical brand “${result.brand}”.`;
        confirmation.value = '';
        status.textContent = '';
        applyButton.disabled = true;
        dialog.showModal();
        confirmation.focus();
      } catch (error) {
        window.alert(error.message);
      } finally {
        button.textContent = original;
        button.disabled = false;
      }
    });
  });

  confirmation.addEventListener('input', () => {
    applyButton.disabled = confirmation.value.trim() !== phrase.textContent;
  });
  dialog.querySelector('[data-dialog-cancel]').addEventListener('click', () => dialog.close());
  applyButton.addEventListener('click', async () => {
    const data = new FormData();
    data.set('csrf_token', csrf || '');
    data.set('token', token.value);
    data.set('confirmation', confirmation.value.trim());
    setBusy(applyButton, true, 'Đang sao lưu & xóa…');
    status.textContent = 'Giữ nguyên cửa sổ cho đến khi giao dịch hoàn tất.';
    try {
      const response = await fetch(applyUrl, { method: 'POST', body: data, credentials: 'same-origin' });
      if (!response.ok) throw new Error(await messageFrom(response));
      const result = await response.json();
      window.location.assign(`${productsUrl}?message=${encodeURIComponent(result.message)}`);
    } catch (error) {
      status.textContent = error.message;
      applyButton.textContent = 'Xóa đúng phạm vi này';
      applyButton.disabled = false;
    }
  });
})();
