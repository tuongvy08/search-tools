function searchProducts() {
    const query = document.getElementById('searchQuery').value;
    fetch(`http://127.0.0.1:5000/search?query=${query}`)
        .then(response => response.json())
        .then(data => displayResults(data))
        .catch(error => console.error('An error occurred:', error));
}


function displayResults(products) {
    const resultsTable = document.getElementById('results').getElementsByTagName('tbody')[0];
    resultsTable.innerHTML = ''; // Xóa kết quả cũ

    products.forEach(product => {
        const row = resultsTable.insertRow();
        row.insertCell(0).innerHTML = product.Name;
        row.insertCell(1).innerHTML = product.Code;
        row.insertCell(2).innerHTML = product.Cas;
        row.insertCell(3).innerHTML = product.Brand;
        row.insertCell(4).innerHTML = product.Size;
        row.insertCell(5).innerHTML = product.Unit_Price;
        row.insertCell(6).innerHTML = product.Note;
    });
}
