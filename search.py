from flask import Flask, jsonify, request, render_template, redirect, url_for, session
import sqlite3
import json
from flask_cors import CORS
import os

from middleware_access import register_ip_access_control  # Kiểm soát IP & vai trò

app = Flask(__name__)
CORS(app)
app.secret_key = 'supersecretkey'

# Kích hoạt middleware kiểm soát IP & vai trò
register_ip_access_control(app, base_path='/home/deploy/myapps')

# Đường dẫn database dùng chung
DB_PATH = '/home/deploy/myapps/shared_data/products.db'

# Lấy tỷ giá
def get_exchange_rate(brand):
    with open(os.path.join(app.root_path, 'static', 'exchange_rates.json'), 'r', encoding='utf-8') as file:
        exchange_rates = json.load(file)
        return exchange_rates.get(brand, 1)

# Đăng nhập có phân quyền
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        password = request.form['password']
        if password == 'Truong@2004':
            session['authenticated'] = True
            session['role'] = 'manager'
            return redirect(url_for('home'))
        elif password == 'Truong@123':
            session['authenticated'] = True
            session['role'] = 'staff'
            return redirect(url_for('home'))
        else:
            return "Incorrect password!", 403
    return render_template('login.html')

# Trang chủ
@app.route('/')
def home():
    if not session.get('authenticated'):
        return redirect(url_for('login'))
    return render_template('index.html')

# API tìm kiếm sản phẩm
@app.route('/search', methods=['GET'])
def search_products():
    search_query = request.args.get('query')
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

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
    return jsonify({'results': results})

# API kiểm tra CAS đặc biệt
@app.route('/check_cas', methods=['GET'])
def check_cas():
    cas = request.args.get('cas')
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    query = '''SELECT brand 
               FROM products 
               WHERE cas = ? AND brand IN ("CẤM NHẬP", "Phụ lục II", "TỒN KHO", "Phụ lục I")'''
    cursor.execute(query, (cas,))
    result = cursor.fetchone()

    warning = None
    if result:
        brand = result[0]
        warning = brand

    conn.close()

    if warning:
        return jsonify({
            'warning': True,
            'warning_type': warning,
            'message': f'CAS {cas} thuộc danh mục {warning}.'
        })
    else:
        return jsonify({ 'warning': False })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
