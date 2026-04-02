let searchResults = [];
let warningsDisplayed = false;

const COMPLIANCE_CLASS = {
    'CẤM NHẬP': 'warning-cam-nhap',
    'Phụ lục II': 'warning-phu-luc-ii',
    'Phụ lục III': 'warning-phu-luc-iii',
    'TỒN KHO': 'warning-ton-kho',
};

function searchProducts() {
    const query = $('#searchQuery').val();
    if (query.trim() === '') {
        alert('Please enter a search query.');
        return;
    }

    warningsDisplayed = false;

    $.getJSON(`/search?query=${encodeURIComponent(query)}`, function(data) {
        searchResults = data.results;
        updateBrandFilterOptions();
        updateSizeFilterOptions();
        displayResults(searchResults);
    }).fail(function(xhr) {
        const msg = xhr && xhr.responseText ? xhr.responseText : 'Unknown error';
        alert(`Search request failed: ${msg}`);
        console.error('Search request failed', xhr);
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

    if (!warningsDisplayed && products === searchResults) {
        showComplianceWarnings(products);
    }
}

function showComplianceWarnings(products) {
    const warningSet = new Set();

    products.forEach(product => {
        if (product.Compliance_Status) {
            const detail = product.Compliance_Note ? ` | ${product.Compliance_Note}` : '';
            warningSet.add(`${product.Compliance_Status}: ${product.Cas || product.Name || product.Code || 'không rõ mã'}${detail}`);
        }
    });

    if (warningSet.size > 0) {
        alert(Array.from(warningSet).join('\n'));
    }

    warningsDisplayed = true;
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
        // Trở về màn search mặc định
        $('.filter-container').show();
        $('#results').show();
        window.__multiMode = null;
    });

    $('#multiRunBtn').on('click', function() {
        const mode = window.__multiMode;
        const text = ($('#multiInput').val() || '').trim();
        if (!mode) {
            alert('Chọn chế độ trước (Check license hoặc Find Code).');
            return;
        }
        if (!text) {
            alert('Vui lòng dán danh sách vào ô input.');
            return;
        }

        if (mode === 'license') {
            $('#licenseWarnings').show().html('<div>Đang kiểm tra...</div>');
            $.ajax({
                url: '/check_cas_batch',
                method: 'POST',
                data: { cas: text },
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
                },
                error: function(xhr) {
                    const msg = xhr && xhr.responseText ? xhr.responseText : 'Unknown error';
                    $('#licenseWarnings').html(`<div style="color:#b00020;">${msg}</div>`);
                }
            });
            return;
        }

        if (mode === 'findcode') {
            $.ajax({
                url: '/find_code_batch',
                method: 'POST',
                data: { codes: text },
                success: function(data) {
                    const products = (data && data.results) ? data.results : [];
                    searchResults = products;
                    warningsDisplayed = false;
                    updateBrandFilterOptions();
                    updateSizeFilterOptions();
                    displayResults(searchResults);
                    $('#multiInput').val('');
                },
                error: function(xhr) {
                    const msg = xhr && xhr.responseText ? xhr.responseText : 'Unknown error';
                    alert(`Find code request failed: ${msg}`);
                }
            });
        }
    });
});
