document.querySelectorAll('.permission-picker').forEach((picker) => {
    const labels = { VIEW_PRICE: 'Unit Price', SEARCH_BY_CAS: 'Tìm bằng CAS', VIEW_COMPLIANCE: 'Tình trạng quản lý' };
    function update() {
        picker.querySelectorAll('[data-dependencies]').forEach((input) => {
            const dependencies = JSON.parse(input.dataset.dependencies);
            const missing = dependencies.filter((key) => !picker.querySelector(`input[value="${key}"]`)?.checked);
            const status = input.closest('label').querySelector('[data-dependency-status]');
            if (status) status.textContent = missing.length ? `Cần bật: ${missing.map((key) => labels[key]).join(', ')}` : '';
            input.closest('label').classList.toggle('is-dependent', input.checked && missing.length > 0);
        });
    }
    picker.addEventListener('change', update);
    update();
});
