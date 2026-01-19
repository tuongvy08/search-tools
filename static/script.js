let searchResults = [];
let warningsDisplayed = false; // Biến để kiểm soát việc hiển thị cảnh báo

function searchProducts() {
    const query = $('#searchQuery').val();
    if (query.trim() === '') {
        alert('Please enter a search query.');
        return;
    }

    // Reset cảnh báo khi thực hiện tìm kiếm mới
    warningsDisplayed = false;

    $.getJSON(`/search?query=${encodeURIComponent(query)}`, function(data) {
        searchResults = data.results;
        updateBrandFilterOptions();
        updateSizeFilterOptions(); // Gọi hàm cập nhật checkboxes cho Size
        displayResults(searchResults);
    });
}

function updateBrandFilterOptions() {
    const brandSet = new Set();
    searchResults.forEach(product => {
        if (product.Brand) {
            brandSet.add(product.Brand);
        }
    });

    const brands = Array.from(brandSet).sort(); // Chuyển sang mảng và sắp xếp A-Z

    const brandCheckboxesContainer = document.getElementById('brandCheckboxes');
    brandCheckboxesContainer.innerHTML = ''; // Xóa các checkbox cũ

    brands.forEach(brand => {
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.id = `brand_${brand.replace(/\s+/g, '_')}`; // Thay thế khoảng trắng bằng dấu gạch dưới
        checkbox.name = 'brand';
        checkbox.value = brand;
        checkbox.addEventListener('change', filterResultsByBrand);

        const label = document.createElement('label');
        label.htmlFor = `brand_${brand.replace(/\s+/g, '_')}`;
        label.textContent = brand;

        // Áp dụng các lớp CSS nếu cần
        const specialBrands = {
            'CẤM NHẬP': 'brand-cam-nhap',
            'Phụ lục III': 'brand-phu-luc-iii',
            'Phụ lục II': 'brand-phu-luc-ii',
            'TỒN KHO': 'brand-ton-kho'
        };

        const cssClass = specialBrands[brand] || '';
        if (cssClass) {
            label.classList.add('button-brand', cssClass);
        }

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

    const sizes = Array.from(sizeSet).sort(); // Chuyển sang mảng và sắp xếp A-Z

    const sizeCheckboxesContainer = document.getElementById('sizeCheckboxes');
    sizeCheckboxesContainer.innerHTML = ''; // Xóa các checkbox cũ

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

function displayResults(products) {
    const resultsTable = document.getElementById('results').getElementsByTagName('tbody')[0];
    resultsTable.innerHTML = ''; // Xóa kết quả cũ

    products.forEach(product => {
        const row = resultsTable.insertRow();

        // Chèn các ô dữ liệu
        row.insertCell(0).innerHTML = product.Name;
        row.insertCell(1).innerHTML = product.Code;
        row.insertCell(2).innerHTML = product.Cas;

        // Tạo nút cho ô Brand
        const brandCell = row.insertCell(3);
        const specialBrands = {
            'CẤM NHẬP': 'brand-cam-nhap',
            'Phụ lục II': 'brand-phu-luc-ii',
            'Phụ lục I': 'brand-phu-luc-i',
            'TỒN KHO': 'brand-ton-kho'
        };

        const cssClass = specialBrands[product.Brand] || '';

        if (cssClass) {
            const brandButton = document.createElement('span');
            brandButton.classList.add('button-brand', cssClass);
            brandButton.textContent = product.Brand;
            brandCell.appendChild(brandButton);
        } else {
            brandCell.textContent = product.Brand;
        }

        row.insertCell(4).innerHTML = product.Size;
        row.insertCell(5).innerHTML = product.Unit_Price;
        row.insertCell(6).innerHTML = product.Note;
    });

    // Chỉ gọi hàm checkCASForWarnings nếu chưa hiển thị cảnh báo và đây là kết quả tìm kiếm mới
    if (!warningsDisplayed && products === searchResults) {
        checkCASForWarnings(products);
    }
}

function checkCASForWarnings(products) {
    const foundCAS = new Set(); // Để theo dõi các CAS đã xử lý
    const casPromises = []; // Mảng chứa các promise fetch

    products.forEach(product => {
        const casToCheck = product.Cas.trim(); // Lấy CAS của mỗi sản phẩm
        if (casToCheck && !foundCAS.has(casToCheck)) {
            foundCAS.add(casToCheck);
            casPromises.push(
                fetch(`/check_cas?cas=${encodeURIComponent(casToCheck)}`)
                    .then(response => response.json())
                    .then(data => {
                        if (data.warning) {
                            data.cas = casToCheck; // Đính kèm CAS vào dữ liệu
                            return data;
                        }
                    })
                    .catch(error => console.error('An error occurred while checking CAS:', error))
            );
        }
    });

    // Chờ tất cả các kiểm tra CAS hoàn thành
    Promise.all(casPromises).then(results => {
        const messages = [];
        results.forEach(data => {
            if (data && data.warning) {
                messages.push(data.message);
                applyWarningClassToRows(data.cas, data.warning_type);
            }
        });

        if (messages.length > 0) {
            // Hiển thị cảnh báo một lần với tất cả các thông báo
            alert(messages.join('\n'));
        }
        // Đặt warningsDisplayed = true bất kể có cảnh báo hay không
        warningsDisplayed = true; // Đánh dấu đã hiển thị cảnh báo
    });
}

function applyWarningClassToRows(cas, warningType) {
    const resultsTable = document.getElementById('results').getElementsByTagName('tbody')[0];

    let cssClass;
    if (warningType === 'CẤM NHẬP') {
        cssClass = 'warning-cam-nhap';
    } else if (warningType === 'Phụ lục II') {
        cssClass = 'warning-phu-luc-ii';
    } else if (warningType === 'Phụ lục I') {
        cssClass = 'warning-phu-luc-i';
    } else if (warningType === 'TỒN KHO') {
        cssClass = 'warning-ton-kho';
    }

    for (let i = 0; i < resultsTable.rows.length; i++) {
        const row = resultsTable.rows[i];
        const casInRow = row.cells[2].innerText.trim();

        if (casInRow === cas) {
            row.classList.add(cssClass);
        }
    }
}

function filterResultsByBrand() {
    updateSizeFilterOptions(); // Cập nhật lại các tùy chọn Size
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
    // Xử lý phím Enter trong ô tìm kiếm
    $('#searchQuery').on('keypress', function(event) {
        if (event.key === 'Enter') {
            event.preventDefault();
            searchProducts();
        }
    });

    // Sự kiện click cho nút "Search"
    $('.search-button').on('click', function() {
        searchProducts();
    });
});
