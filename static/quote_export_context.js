(function () {
    const root = document.getElementById('quoteExportContext');
    if (!root) {
        window.QuoteExportContext = { params: () => ({}) };
        return;
    }
    const team = root.querySelector('[data-quote-team]');
    const template = root.querySelector('[data-quote-template]');
    const status = root.querySelector('[data-quote-context-status]');
    const params = () => ({
        team_id: team && team.value ? Number(team.value) : null,
        template_id: template && template.value ? Number(template.value) : null,
    });
    const option = (value, label) => {
        const node = document.createElement('option');
        node.value = value == null ? '' : String(value);
        node.textContent = label;
        return node;
    };
    fetch('/api/admin/quote-template-contexts', { credentials: 'same-origin' })
        .then(async (response) => {
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.error || 'Không tải được phạm vi xuất báo giá.');
            (data.teams || []).filter((item) => item.status === 'ACTIVE').forEach((item) =>
                team.appendChild(option(item.id, item.name)));
            (data.templates || []).filter((item) => !item.archived_at).forEach((item) =>
                template.appendChild(option(item.id, `#${item.id} · ${item.filename}${item.is_active ? ' · Mẫu mặc định toàn hệ thống' : ''}`)));
            status.textContent = 'Hệ thống sẽ kiểm tra lại phạm vi khi xuất.';
        })
        .catch((error) => { status.textContent = error.message; root.classList.add('is-error'); });
    root.addEventListener('change', () => document.dispatchEvent(new CustomEvent('quote-context-change')));
    window.QuoteExportContext = { params };
})();
