from flask import Flask, jsonify, request, render_template
import sqlite3
import json
from flask_cors import CORS
import os

app = Flask(__name__)
CORS(app)

# Đường dẫn tuyệt đối tới database trên VPS
DB_PATH = '/home/deploy/myapps/shared_data/products.db'

# Hàm lấy tỷ giá
def get_exchange_rate(brand):
    with open(os.path.join(app.root_path, 'static', 'exchange_rates.json'), 'r', encoding='utf-8') as file:
        exchange_rates = json.load(file)
        return exchange_rates.get(brand, 1)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/search', methods=['GET'])
def search_products():
    search_query = request.args.get('query')

    # Sử dụng đường dẫn tuyệt đối tới database
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    query = 'SELECT name, code, cas, brand, size, ship, price, note FROM products WHERE code LIKE ? OR cas LIKE ?'
    cursor.execute(query, (f"%{search_query}%", f"%{search_query}%"))
    products = cursor.fetchall()

    results = []
    for product in products:
        name, code, cas, brand, size, ship, price, note = product
        ship = ship if ship is not None else 0
        price = price if price is not None else 0

        exchange_rate = get_exchange_rate(brand)
        unit_price = round(price * ship * exchange_rate, -3)
        formatted_unit_price = '{:,.0f}'.format(unit_price)

        results.append({
            'Name': name,
            'Code': code,
            'Cas': cas,
            'Brand': brand,
            'Size': size,
            'Unit_Price': formatted_unit_price,
            'Note': note
        })

    conn.close()
    return jsonify(results)

if __name__ == '__main__':
    app.run(debug=True)
