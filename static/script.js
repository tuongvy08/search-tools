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
        return `<strong>Compliance:</strong> ${complianceNote}`;
    }
    return `${mainNote}<br><small><strong>Compliance:</strong> ${complianceNote}</small>`;
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
        setMultiMode('license');
        $('#multiInput').focus();
    });

    $('#btnFindCode').on('click', function() {
        setMultiMode('findcode');
        $('#multiInput').focus();
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
