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
    return { can, field, transfer };
})();
