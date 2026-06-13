let searchResults = [];

const COMPLIANCE_CLASS = {
    'CẤM NHẬP': 'warning-cam-nhap',
    'Phụ lục II': 'warning-phu-luc-ii',
    'Phụ lục III': 'warning-phu-luc-iii',
    'TỒN KHO': 'warning-ton-kho',
};

const AJAX_LONG_TIMEOUT_MS = 180000;

function countBatchItems(text) {
    const out = [];
    for (const line of String(text).split(/\n/)) {
        const t = line.trim();
        if (!t || t.startsWith('#')) continue;
        for (const part of t.replace(/;/g, ',').split(',')) {
            const item = part.trim();
            if (item) out.push(item);
        }
    }
    return Math.min(out.length, 2000);
}

function setOperationStatus(html, kind) {
    const el = $('#operationStatus');
    el.removeClass('is-loading is-error is-success');
    if (!html) {
        el.hide().empty();
        return;
    }
    if (kind === 'loading') el.addClass('is-loading');
    else if (kind === 'error') el.addClass('is-error');
    else if (kind === 'success') el.addClass('is-success');
    el.html(html).show();
}

function formatAjaxError(xhr, defaultMsg) {
    const status = xhr && xhr.status;
    if (status === 504) {
        return 'Hết thời gian chờ máy chủ (504). Danh sách có thể quá dài hoặc tải cao — thử giảm số dòng mỗi lần, hoặc kiểm tra index DB và timeout nginx/gunicorn.';
    }
    if (status === 403) {
        return 'Truy cập bị từ chối (403). Kiểm tra IP allowlist hoặc đăng nhập.';
    }
    if (status === 502) {
        return 'Lỗi 502 Bad Gateway: nginx không nhận phản hồi từ app (Gunicorn có thể crash, tắt, hoặc bị timeout). Trên VPS chạy: sudo systemctl status search-tools-pg và journalctl -u search-tools-pg -n 80. Thường cần tăng --timeout trong lệnh gunicorn (ví dụ 120).';
    }
    if (status === 0 || status === 503) {
        return 'Không kết nối được tới máy chủ. Thử lại sau vài giây.';
    }
    let body = (xhr && xhr.responseText) ? String(xhr.responseText).trim() : '';
    if (body.length > 280) body = body.slice(0, 280) + '…';
    if (body && body.startsWith('<')) body = defaultMsg;
    return body || defaultMsg;
}

function setBatchRunning(running) {
    const btn = $('#multiRunBtn');
    if (running) {
        btn.prop('disabled', true).data('prev-label', btn.text()).text('Đang xử lý…');
    } else {
        const prev = btn.data('prev-label');
        btn.prop('disabled', false);
        if (prev) btn.text(prev);
    }
}

function _excelSafeCell(value) {
    const s = String(value ?? '').replace(/\r?\n/g, ' ').trim();
    // Ngăn Excel hiểu nhầm công thức khi paste.
    if (/^[=+\-@]/.test(s)) {
        return "'" + s;
    }
    return s;
}

function copyCurrentTableToClipboard() {
    const table = document.getElementById('results');
    if (!table) {
        setOperationStatus('Không tìm thấy bảng kết quả để export.', 'error');
        return;
    }
    const rows = table.querySelectorAll('tbody tr');
    if (!rows.length) {
        setOperationStatus('Chưa có dữ liệu để export sang Excel.', 'error');
        return;
    }

    const headers = Array.from(table.querySelectorAll('thead th')).map((th) => _excelSafeCell(th.textContent));
    const lines = [headers.join('\t')];
    rows.forEach((tr) => {
        const cells = Array.from(tr.querySelectorAll('td')).map((td) => _excelSafeCell(td.textContent));
        lines.push(cells.join('\t'));
    });
    const payload = lines.join('\n');

    const done = () => {
        setOperationStatus(`Đã copy <strong>${rows.length}</strong> dòng kết quả. Mở Excel và dán (Ctrl/Cmd + V).`, 'success');
    };

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(payload).then(done).catch(() => {
            const ta = document.createElement('textarea');
            ta.value = payload;
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.select();
            document.execCommand('copy');
            document.body.removeChild(ta);
            done();
        });
        return;
    }

    const ta = document.createElement('textarea');
    ta.value = payload;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    done();
}

function searchProducts() {
    const query = $('#searchQuery').val();
    if (query.trim() === '') {
        setOperationStatus('Nhập từ khóa tìm kiếm.', 'error');
        return;
    }

    setOperationStatus('<span class="status-spinner"></span> Đang tìm kiếm…', 'loading');

    $.ajax({
        url: `/search?query=${encodeURIComponent(query)}`,
        dataType: 'json',
        timeout: AJAX_LONG_TIMEOUT_MS,
        success: function(data) {
            searchResults = data.results || [];
            updateBrandFilterOptions();
            updateSizeFilterOptions();
            displayResults(searchResults);
            const n = searchResults.length;
            setOperationStatus(
                n ? `Tìm thấy <strong>${n}</strong> dòng.` : 'Không có kết quả.',
                n ? 'success' : ''
            );
            if (!n) setTimeout(() => setOperationStatus('', ''), 4000);
        },
        error: function(xhr) {
            const msg = formatAjaxError(xhr, 'Tìm kiếm thất bại.');
            setOperationStatus(msg, 'error');
            console.error('Search request failed', xhr);
        },
    });
}

function updateBrandFilterOptions() {
    const brandSet = new Set();
    searchResults.forEach(product => {
        if (product.Brand) {
            brandSet.add(product.Brand);
        }
    });

    const brands = Array.from(brandSet).sort();
    const brandCheckboxesContainer = document.getElementById('brandCheckboxes');
    brandCheckboxesContainer.innerHTML = '';

    brands.forEach(brand => {
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.id = `brand_${brand.replace(/\s+/g, '_')}`;
        checkbox.name = 'brand';
        checkbox.value = brand;
        checkbox.addEventListener('change', filterResultsByBrand);

        const label = document.createElement('label');
        label.htmlFor = `brand_${brand.replace(/\s+/g, '_')}`;
        label.textContent = brand;

        const container = document.createElement('div');
        container.appendChild(checkbox);
        container.appendChild(label);
        brandCheckboxesContainer.appendChild(container);
    });
}

function updateSizeFilterOptions() {
    const brandCheckboxes = document.querySelectorAll('#brandCheckboxes input[name="brand"]:checked');
    const selectedBrands = Array.from(brandCheckboxes).map(checkbox => checkbox.value);

    let filteredProducts = searchResults;

    if (selectedBrands.length > 0) {
        filteredProducts = searchResults.filter(product => selectedBrands.includes(product.Brand));
    }

    const sizeSet = new Set();
    filteredProducts.forEach(product => {
        if (product.Size) {
            sizeSet.add(product.Size);
        }
    });

    const sizes = Array.from(sizeSet).sort();
    const sizeCheckboxesContainer = document.getElementById('sizeCheckboxes');
    sizeCheckboxesContainer.innerHTML = '';

    sizes.forEach(size => {
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.id = `size_${size.replace(/\s+/g, '_')}`;
        checkbox.name = 'size';
        checkbox.value = size;
        checkbox.addEventListener('change', filterResultsBySize);

        const label = document.createElement('label');
        label.htmlFor = `size_${size.replace(/\s+/g, '_')}`;
        label.textContent = size;

        const container = document.createElement('div');
        container.appendChild(checkbox);
        container.appendChild(label);
        sizeCheckboxesContainer.appendChild(container);
    });
}

function badgeForCompliance(label) {
    if (!label) {
        return '';
    }
    return `<span class="button-brand">${label}</span>`;
}

function formatNoteCell(product) {
    const mainNote = product.Note || '';
    const complianceNote = product.Compliance_Note || '';
    if (!complianceNote) {
        return mainNote;
    }
    if (!mainNote) {
        return `Compliance: ${complianceNote}`;
    }
    // Giữ 1 dòng để copy sang Excel không bị xuống dòng trong cùng ô.
    return `${mainNote} | Compliance: ${complianceNote}`;
}

function displayResults(products) {
    const resultsTable = document.getElementById('results').getElementsByTagName('tbody')[0];
    resultsTable.innerHTML = '';

    products.forEach(product => {
        const row = resultsTable.insertRow();
        row.insertCell(0).innerHTML = product.Name || '';
        row.insertCell(1).innerHTML = product.Code || '';
        row.insertCell(2).innerHTML = product.Cas || '';
        row.insertCell(3).innerHTML = product.Brand || '';
        row.insertCell(4).innerHTML = product.Size || '';
        row.insertCell(5).innerHTML = product.Unit_Price || '';
        row.insertCell(6).innerHTML = formatNoteCell(product);
        row.insertCell(7).innerHTML = badgeForCompliance(product.Compliance_Status);

        const cssClass = product.Compliance_Css || COMPLIANCE_CLASS[product.Compliance_Status];
        if (cssClass) {
            row.classList.add(cssClass);
        }
    });
}

function filterResultsByBrand() {
    updateSizeFilterOptions();
    filterResults();
}

function filterResultsBySize() {
    filterResults();
}

function filterResults() {
    const sizeCheckboxes = document.querySelectorAll('#sizeCheckboxes input[name="size"]:checked');
    const selectedSizes = Array.from(sizeCheckboxes).map(checkbox => checkbox.value);

    const brandCheckboxes = document.querySelectorAll('#brandCheckboxes input[name="brand"]:checked');
    const selectedBrands = Array.from(brandCheckboxes).map(checkbox => checkbox.value);

    let filteredResults = searchResults;

    if (selectedBrands.length > 0) {
        filteredResults = filteredResults.filter(product => selectedBrands.includes(product.Brand));
    }

    if (selectedSizes.length > 0) {
        filteredResults = filteredResults.filter(product => selectedSizes.includes(product.Size));
    }

    displayResults(filteredResults);
}

let advOptionsData = { brands: [], size_pairs: [] };

function openAdvancedPanel() {
    $('#multiModePanel').hide();
    $('#licenseWarnings').hide().html('');
    window.__multiMode = null;
    $('#advancedSearchPanel').show();
    $('.filter-container').show();
    $('#results').show();
    setOperationStatus('', '');
}

function closeAdvancedPanel() {
    $('#advancedSearchPanel').hide();
    $('#advCasInput').val('');
    $('#advBrandCheckboxes').empty();
    $('#advSizeCheckboxes').empty();
    $('#advSizeFilter').val('');
    $('#advSizeFuzzy').prop('checked', false);
    advOptionsData = { brands: [], size_pairs: [] };
    setOperationStatus('', '');
}

function getAdvSelectedBrands() {
    return Array.from(document.querySelectorAll('#advBrandCheckboxes input[name="adv_brand"]:checked'))
        .map((el) => el.value);
}

function getAdvSelectedSizes() {
    return Array.from(document.querySelectorAll('#advSizeCheckboxes input[name="adv_size"]:checked'))
        .map((el) => el.value);
}

function renderAdvBrandCheckboxes(brands) {
    const container = document.getElementById('advBrandCheckboxes');
    container.innerHTML = '';
    brands.forEach((item) => {
        const brand = item.brand || '';
        const count = item.row_count || 0;
        const id = `adv_brand_${brand.replace(/[^a-zA-Z0-9]+/g, '_')}`;
        const wrap = document.createElement('label');
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.name = 'adv_brand';
        cb.id = id;
        cb.value = brand;
        cb.addEventListener('change', refreshAdvSizeCheckboxes);
        wrap.appendChild(cb);
        wrap.appendChild(document.createTextNode(`${brand} (${count})`));
        container.appendChild(wrap);
    });
}

function aggregateAdvSizes(sizePairs, selectedBrands) {
    const brandSet = new Set(selectedBrands);
    const bySize = new Map();
    sizePairs.forEach((pair) => {
        if (brandSet.size && !brandSet.has(pair.brand)) {
            return;
        }
        const size = pair.size || '';
        if (!size) return;
        const prev = bySize.get(size) || { size, row_count: 0, cas_hits: 0 };
        prev.row_count += pair.row_count || 0;
        prev.cas_hits += pair.cas_hits || 0;
        bySize.set(size, prev);
    });
    return Array.from(bySize.values()).sort((a, b) => {
        if (b.row_count !== a.row_count) return b.row_count - a.row_count;
        return a.size.localeCompare(b.size, undefined, { sensitivity: 'base' });
    });
}

function renderAdvSizeCheckboxes(sizes) {
    const filterText = ($('#advSizeFilter').val() || '').trim().toLowerCase();
    const container = document.getElementById('advSizeCheckboxes');
    container.innerHTML = '';
    sizes.forEach((item) => {
        const size = item.size || '';
        if (filterText && !size.toLowerCase().includes(filterText)) {
            return;
        }
        const id = `adv_size_${size.replace(/[^a-zA-Z0-9]+/g, '_')}`;
        const wrap = document.createElement('label');
        wrap.dataset.sizeLabel = size;
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.name = 'adv_size';
        cb.id = id;
        cb.value = size;
        wrap.appendChild(cb);
        wrap.appendChild(document.createTextNode(`${size} (${item.row_count})`));
        container.appendChild(wrap);
    });
    if (!container.children.length) {
        container.innerHTML = '<span class="adv-hint">Không có size phù hợp. Thử bỏ lọc hoặc chọn thêm brand.</span>';
    }
}

function refreshAdvSizeCheckboxes() {
    const selectedBrands = getAdvSelectedBrands();
    const sizes = aggregateAdvSizes(advOptionsData.size_pairs || [], selectedBrands);
    renderAdvSizeCheckboxes(sizes);
}

function loadAdvancedOptions() {
    const casText = ($('#advCasInput').val() || '').trim();
    if (!casText) {
        setOperationStatus('Bước 1: vui lòng dán danh sách CAS.', 'error');
        return;
    }
    const nCas = countBatchItems(casText);
    setOperationStatus(
        `<span class="status-spinner"></span> Đang tải brand &amp; size cho <strong>${nCas}</strong> CAS…`,
        'loading'
    );
    $('#advLoadOptionsBtn').prop('disabled', true);
    $.ajax({
        url: '/advanced_search/options',
        method: 'POST',
        data: { cas: casText },
        timeout: AJAX_LONG_TIMEOUT_MS,
        success: function(data) {
            $('#advLoadOptionsBtn').prop('disabled', false);
            if (data && data.error) {
                setOperationStatus(String(data.error), 'error');
                return;
            }
            advOptionsData = {
                brands: data.brands || [],
                size_pairs: data.size_pairs || [],
            };
            renderAdvBrandCheckboxes(advOptionsData.brands);
            refreshAdvSizeCheckboxes();
            const nb = advOptionsData.brands.length;
            const ns = aggregateAdvSizes(advOptionsData.size_pairs, []).length;
            setOperationStatus(
                `Đã tải <strong>${nb}</strong> brand, <strong>${ns}</strong> size khả dụng cho danh sách CAS.`,
                'success'
            );
        },
        error: function(xhr) {
            $('#advLoadOptionsBtn').prop('disabled', false);
            setOperationStatus(formatAjaxError(xhr, 'Tải brand/size thất bại.'), 'error');
        },
    });
}

function runAdvancedSearch() {
    const casText = ($('#advCasInput').val() || '').trim();
    if (!casText) {
        setOperationStatus('Bước 1: vui lòng dán danh sách CAS.', 'error');
        return;
    }
    const brands = getAdvSelectedBrands();
    const sizes = getAdvSelectedSizes();
    const sizeFuzzy = $('#advSizeFuzzy').is(':checked') ? '1' : '0';
    const nCas = countBatchItems(casText);
    setOperationStatus(
        `<span class="status-spinner"></span> Đang tìm <strong>${nCas}</strong> CAS…`,
        'loading'
    );
    $('#advRunBtn').prop('disabled', true);
    $.ajax({
        url: '/advanced_search',
        method: 'POST',
        traditional: true,
        data: {
            cas: casText,
            brands: brands,
            sizes: sizes,
            size_fuzzy: sizeFuzzy,
        },
        timeout: AJAX_LONG_TIMEOUT_MS,
        success: function(data) {
            $('#advRunBtn').prop('disabled', false);
            if (data && data.error) {
                setOperationStatus(String(data.error), 'error');
                return;
            }
            const products = (data && data.results) ? data.results : [];
            searchResults = products;
            updateBrandFilterOptions();
            updateSizeFilterOptions();
            displayResults(searchResults);
            const matched = data && data.matched_cas != null ? data.matched_cas : 0;
            const total = data && data.total_cas != null ? data.total_cas : nCas;
            setOperationStatus(
                `Hoàn tất: <strong>${products.length}</strong> dòng — <strong>${matched}/${total}</strong> CAS có sản phẩm khớp bộ lọc.`,
                'success'
            );
        },
        error: function(xhr) {
            $('#advRunBtn').prop('disabled', false);
            setOperationStatus(formatAjaxError(xhr, 'Advanced search thất bại.'), 'error');
        },
    });
}

$(document).ready(function() {
    if (!$('#operationStatus').length) {
        $('.search-container').after(
            '<div id="operationStatus" class="operation-status" role="status" aria-live="polite"></div>'
        );
    }

    $('#searchQuery').on('keypress', function(event) {
        if (event.key === 'Enter') {
            event.preventDefault();
            searchProducts();
        }
    });

    $('.search-button').on('click', function() {
        searchProducts();
    });
    $('#btnCopyExcel').on('click', function() {
        copyCurrentTableToClipboard();
    });

    function setMultiMode(mode) {
        $('#multiModePanel').show();
        $('#licenseWarnings').hide().html('');

        if (mode === 'license') {
            $('#multiModeTitle').text('Check license (CAS list)');
            $('#multiInput').attr('placeholder', 'Paste CAS (mỗi dòng 1 CAS)');
            $('.filter-container').hide();
            $('#results').hide();
        } else if (mode === 'findcode') {
            $('#multiModeTitle').text('Find code (Code list)');
            $('#multiInput').attr('placeholder', 'Paste code (mỗi dòng 1 code)');
            $('.filter-container').show();
            $('#results').show();
        }
        window.__multiMode = mode;
    }

    $('#btnCheckLicense').on('click', function() {
        $('#advancedSearchPanel').hide();
        setMultiMode('license');
        $('#multiInput').focus();
    });

    $('#btnFindCode').on('click', function() {
        $('#advancedSearchPanel').hide();
        setMultiMode('findcode');
        $('#multiInput').focus();
    });

    $('#btnAdvancedSearch').on('click', function() {
        openAdvancedPanel();
        $('#advCasInput').focus();
    });

    $('#advCancelBtn').on('click', function() {
        closeAdvancedPanel();
    });

    $('#advLoadOptionsBtn').on('click', loadAdvancedOptions);
    $('#advRunBtn').on('click', runAdvancedSearch);
    $('#advSizeFilter').on('input', refreshAdvSizeCheckboxes);
    $('#advSizeSelectAll').on('click', function() {
        document.querySelectorAll('#advSizeCheckboxes input[name="adv_size"]').forEach((el) => {
            el.checked = true;
        });
    });
    $('#advSizeClearAll').on('click', function() {
        document.querySelectorAll('#advSizeCheckboxes input[name="adv_size"]').forEach((el) => {
            el.checked = false;
        });
    });

    $('#multiCancelBtn').on('click', function() {
        $('#multiModePanel').hide();
        $('#licenseWarnings').hide().html('');
        $('#multiInput').val('');
        setOperationStatus('', '');
        setBatchRunning(false);
        // Trở về màn search mặc định
        $('.filter-container').show();
        $('#results').show();
        window.__multiMode = null;
    });

    $('#multiRunBtn').on('click', function() {
        const mode = window.__multiMode;
        const text = ($('#multiInput').val() || '').trim();
        if (!mode) {
            setOperationStatus('Chọn <strong>Check license</strong> hoặc <strong>Find Code</strong> trước khi bấm Run.', 'error');
            return;
        }
        if (!text) {
            setOperationStatus('Vui lòng dán danh sách vào ô nhập.', 'error');
            return;
        }

        if (mode === 'license') {
            const nCas = countBatchItems(text);
            $('#licenseWarnings').show().html(
                `<div class="batch-inline-loading"><span class="status-spinner"></span> Đang kiểm tra <strong>${nCas}</strong> CAS…</div>`
            );
            setBatchRunning(true);
            $.ajax({
                url: '/check_cas_batch',
                method: 'POST',
                data: { cas: text },
                timeout: AJAX_LONG_TIMEOUT_MS,
                success: function(data) {
                    const items = data && data.results ? data.results : [];
                    let warnCount = 0;

                    let rowsHtml = '';
                    items.forEach(item => {
                        const status = item.Compliance_Status || '';
                        const note = item.Compliance_Note || '';
                        if (status) warnCount += 1;
                        rowsHtml += `
                          <tr>
                            <td>${item.Cas || ''}</td>
                            <td>${status}</td>
                            <td>${note}</td>
                          </tr>
                        `;
                    });

                    const summary = `<div class="hint" style="margin-bottom:10px; font-weight:600;">Cảnh báo: ${warnCount}/${items.length}</div>`;
                    const tableHtml = `
                      <table class="license-table">
                        <thead>
                          <tr>
                            <th>CAS</th>
                            <th>Compliance_Status</th>
                            <th>Compliance_Note</th>
                          </tr>
                        </thead>
                        <tbody>
                          ${rowsHtml}
                        </tbody>
                      </table>
                    `;
                    $('#licenseWarnings').html(summary + tableHtml);
                    $('#multiInput').val('');
                    setBatchRunning(false);
                },
                error: function(xhr) {
                    const msg = formatAjaxError(xhr, 'Kiểm tra CAS thất bại.');
                    $('#licenseWarnings').html(`<div class="batch-error-msg">${$('<div/>').text(msg).html()}</div>`);
                    setBatchRunning(false);
                }
            });
            return;
        }

        if (mode === 'findcode') {
            const nCodes = countBatchItems(text);
            setOperationStatus(
                `<span class="status-spinner"></span> Đang tra cứu <strong>${nCodes}</strong> mã sản phẩm… (có thể mất vài chục giây với danh sách dài)`,
                'loading'
            );
            setBatchRunning(true);
            const runFind = function() {
            $.ajax({
                url: '/find_code_batch',
                method: 'POST',
                data: { codes: text },
                timeout: AJAX_LONG_TIMEOUT_MS,
                success: function(data) {
                    const products = (data && data.results) ? data.results : [];
                    const err = data && data.error;
                    if (err) {
                        setOperationStatus(String(err), 'error');
                        setBatchRunning(false);
                        return;
                    }
                    searchResults = products;
                    updateBrandFilterOptions();
                    updateSizeFilterOptions();
                    displayResults(searchResults);
                    $('#multiInput').val('');
                    const found = products.filter((p) => (p.Name || p.Cas || p.Brand)).length;
                    setOperationStatus(
                        `Hoàn tất: <strong>${products.length}</strong> mã — <strong>${found}</strong> có dữ liệu sản phẩm.`,
                        'success'
                    );
                    setBatchRunning(false);
                },
                error: function(xhr) {
                    const msg = formatAjaxError(xhr, 'Tra cứu mã thất bại.');
                    setOperationStatus(msg, 'error');
                    setBatchRunning(false);
                }
            });
            };
            if (window.requestAnimationFrame) {
                requestAnimationFrame(function() {
                    setTimeout(runFind, 0);
                });
            } else {
                setTimeout(runFind, 0);
            }
        }
    });
});
