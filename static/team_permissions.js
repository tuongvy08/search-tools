/* Server-rendered snapshot for presentation; APIs independently enforce live policy. */
var TeamPermissions = (() => {
    const data = JSON.parse(document.getElementById('teamPermissionData').textContent);
    const keys = new Set(data.keys);
    const dependencies = { QUICK_QUOTE: ['VIEW_PRICE'], CHECK_LICENSE: ['SEARCH_BY_CAS', 'VIEW_COMPLIANCE'], ADVANCED_SEARCH: ['SEARCH_BY_CAS'] };
    const can = (key) => keys.has(key) && (dependencies[key] || []).every((dep) => keys.has(dep));
    const field = (column) => Object.entries(data.fields).some(([key, spec]) => spec[0] === column && can(key));
    async function transfer(action, source, rows) {
        const response = await fetch(`/api/results/${action}`, {
            method: 'POST', credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': data.csrf },
            body: JSON.stringify({ source, rows }),
        });
        if (!response.ok) throw new Error('Quyền truy cập hoặc dữ liệu đã thay đổi. Vui lòng tải lại trang.');
        return action === 'copy' ? (await response.json()).text : response.blob();
    }
    async function quoteExport(selections, context = {}) {
        const body = new FormData();
        body.append('selections', JSON.stringify(selections));
        if (context.team_id) body.append('team_id', String(context.team_id));
        if (context.template_id) body.append('template_id', String(context.template_id));
        const response = await fetch('/api/results/quote-export', {
            method: 'POST', credentials: 'same-origin', headers: { 'X-CSRF-Token': data.csrf }, body,
        });
        if (!response.ok) {
            const payload = await response.json().catch(() => ({}));
            throw new Error(payload.error || 'Không thể xuất báo giá. Vui lòng thử lại.');
        }
        return { blob: await response.blob(), disposition: response.headers.get('Content-Disposition') || '' };
    }
    return { can, field, transfer, quoteExport, csrfToken: () => data.csrf || '' };
})();
