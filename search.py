from flask import Flask, jsonify, request, render_template, redirect, url_for, session
import sqlite3
import json
from flask_cors import CORS
import os

app = Flask(__name__)
CORS(app)
app.secret_key = 'supersecretkey'  # Đặt secret key cho Flask session
PASSWORD = 'truong3344'  # Mật khẩu cần nhập để truy cập trang web

# Đường dẫn tuyệt đối tới database trên VPS
DB_PATH = '/home/deploy/myapps/shared_data/products.db'

# Hàm lấy tỷ giá
def get_exchange_rate(brand):
    with open(os.path.join(app.root_path, 'static', 'exchange_rates.json'), 'r', encoding='utf-8') as file:
        exchange_rates = json.load(file)
        return exchange_rates.get(brand, 1)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form['password'] == PASSWORD:
            session['authenticated'] = True
            return redirect(url_for('home'))
        else:
            return "Incorrect password!", 403
    return render_template('login.html')

@app.route('/')
def home():
    if not session.get('authenticated'):
        return redirect(url_for('login'))
    return render_template('index.html')

@app.route('/search', methods=['GET'])
def search_products():
    search_query = request.args.get('query')

    # Sử dụng đường dẫn tuyệt đối tới database
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Tìm kiếm sản phẩm dựa trên tên, mã hoặc CAS
    query = '''SELECT name, code, cas, brand, size, ship, price, note 
               FROM products 
               WHERE name LIKE ? OR code LIKE ? OR cas LIKE ?'''
    cursor.execute(query, (f"%{search_query}%", f"%{search_query}%", f"%{search_query}%"))
    products = cursor.fetchall()

    results = []
    for product in products:
        name, code, cas, brand, size, ship, price, note = product
        try:
            ship = float(ship) if ship is not None else 0
        except ValueError:
            ship = 0
        try:
            price = float(price) if price is not None else 0
        except ValueError:
            price = 0

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

    response = {
        'results': results
    }

    return jsonify(response)

# API để kiểm tra CAS có thuộc danh mục đặc biệt "CẤM NHẬP" hoặc "Phụ lục II" hay không
@app.route('/check_cas', methods=['GET'])
def check_cas():
    cas = request.args.get('cas')

    conn = sqlite3.connect('products.db')
    cursor = conn.cursor()

    # Kiểm tra nếu CAS thuộc danh mục "CẤM NHẬP", "Phụ lục II", "TỒN KHO", hoặc "Phụ lục I"
    query = '''SELECT brand 
               FROM products 
               WHERE cas = ? AND brand IN ("CẤM NHẬP", "Phụ lục II", "TỒN KHO", "Phụ lục I")'''
    cursor.execute(query, (cas,))
    result = cursor.fetchone()

    warning = None
    if result:
        brand = result[0]
        if brand == "CẤM NHẬP":
            warning = "CẤM NHẬP"
        elif brand == "Phụ lục II":
            warning = "Phụ lục II"
        elif brand == "Phụ lục I":
            warning = "Phụ lục I"
        elif brand == "TỒN KHO":
            warning = "TỒN KHO"

    conn.close()

    if warning:
        return jsonify({
            'warning': True,
            'warning_type': warning,
            'message': f'CAS {cas} thuộc danh mục {warning}.'
        })
    else:
        return jsonify({
            'warning': False
        })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
