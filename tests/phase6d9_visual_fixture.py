"""Local-only visual QA fixture for the Phase 6D9 Product Search template.

This renders the real Jinja template and real static assets with synthetic
permissions. It does not import the production app, open a database, or relax
any production authentication path.
"""

import argparse
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request, session


ROOT = Path(__file__).resolve().parents[1]
app = Flask(
    __name__,
    static_folder=str(ROOT / "static"),
    template_folder=str(ROOT / "templates"),
)
app.secret_key = "phase6d9-local-visual-fixture"

GRANTS = {
    "SEARCH", "CHECK_LICENSE", "FIND_CODE", "ADVANCED_SEARCH", "QUICK_QUOTE",
    "COPY", "EXPORT", "VIEW_NAME", "VIEW_CODE", "VIEW_CAS", "VIEW_BRAND",
    "VIEW_SIZE", "VIEW_PRICE", "VIEW_NOTE", "VIEW_COMPLIANCE",
    "VIEW_COMPLIANCE_NOTE", "SEARCH_BY_CAS",
}
FIELDS = {
    "VIEW_NAME": ("Name", "Name"),
    "VIEW_CODE": ("Code", "Code"),
    "VIEW_CAS": ("Cas", "CAS"),
    "VIEW_BRAND": ("Brand", "Brand"),
    "VIEW_SIZE": ("Size", "Size"),
    "VIEW_PRICE": ("Unit_Price", "Unit Price"),
    "VIEW_NOTE": ("Note", "Note"),
    "VIEW_COMPLIANCE": ("Compliance", "Tình trạng quản lý"),
    "VIEW_COMPLIANCE_NOTE": ("Compliance_Note", "Ghi chú quản lý"),
}


def _active_grants():
    profile = request.args.get("profile", "admin")
    if profile == "no-search":
        return {"FIND_CODE", "VIEW_CODE", "VIEW_BRAND", "VIEW_SIZE"}
    if profile == "search-no-actions":
        return GRANTS - {"COPY", "EXPORT"}
    return GRANTS


def _stub(name):
    def view():
        return f"Synthetic visual fixture: {name}"

    app.add_url_rule(f"/_fixture/{name.replace('.', '-')}", endpoint=name, view_func=view)


for endpoint in (
    "quick_quote", "admin_products", "admin_imports", "admin_teams.index",
    "admin_users", "admin_network", "admin_quote_templates_page",
    "admin_exchange_rates", "admin_regulatory", "admin_stock",
    "admin_brand_compliance", "admin_login_history.index", "session_security.logout",
):
    _stub(endpoint)


@app.context_processor
def context():
    grants = _active_grants()
    return {
        "can": lambda key: key in grants,
        "permission_fields": FIELDS,
        "permission_keys": sorted(grants),
        "csrf_token": lambda: "visual-fixture-csrf",
        "admin_can": lambda _endpoint: True,
        "admin_has_menus": True,
        "admin_is_super": True,
    }


@app.get("/")
def home():
    is_admin = request.args.get("profile", "admin") == "admin"
    session.update(
        authenticated=True,
        is_admin=is_admin,
        username=(
            "sales.operations.with.a.long.identity@labmall.example"
            if is_admin else "restricted.staff@labmall.example"
        ),
    )
    return render_template("index.html")


@app.get("/api/admin/quote-template-contexts")
def quote_contexts():
    return jsonify(
        teams=[
            {"id": 1, "name": "Commercial Operations — Northern Region", "status": "ACTIVE"},
            {"id": 2, "name": "Key Accounts", "status": "ACTIVE"},
        ],
        templates=[
            {
                "id": 7,
                "filename": "Bao-gia-phong-thi-nghiem-ban-chuan-rat-dai.xlsx",
                "is_active": True,
                "archived_at": None,
            }
        ],
    )


@app.get("/search/suggestions")
def suggestions():
    return jsonify(
        suggestions=[
            {"value": "75-21-8", "label": "75-21-8 · Ethylene oxide analytical reference"},
            {"value": "CP-CA-RS-02", "label": "CP-CA-RS-02 · California Category II Residual Solvent Standard"},
        ]
    )


@app.get("/search")
def search_results():
    query = request.args.get("query", "")
    if query == "slow-state":
        time.sleep(0.8)
    if query == "error-state":
        return jsonify(error="Lỗi tổng hợp để kiểm tra trạng thái hiển thị."), 500
    return jsonify(
        results=[
            {
                "product_id": 101,
                "Name": "California Category II Residual Solvent Standard 5 mg/mL in Water — analytical reference material",
                "Code": "CP-CA-RS-02-LONG-REFERENCE-CODE",
                "Cas": "75-21-8",
                "Brand": "AccuStandard International Reference Materials",
                "Size": "10, 25, 50, 100 mg — multi-pack laboratory configuration",
                "Unit_Price": "496,000",
                "Note": "Hạn sử dụng tối thiểu 3 tháng. Xác nhận điều kiện vận chuyển lạnh trước khi gửi báo giá.",
                "Compliance_Status": "Kiểm soát đặc biệt nhóm 1",
                "Compliance_Note": "Hóa chất kiểm soát đặc biệt theo Công ước Rotterdam và Công ước Stockholm; cần giấy phép trước khi xử lý đơn hàng.",
                "Compliance_Css": "regulatory-color-custom",
                "Compliance_Color": "#5B21B6",
                "Compliance_Bg": "#EDE9FE",
                "Compliance_Fg": "#5B21B6",
                "Stock_Options": [
                    {
                        "Stock_Quantity": 12,
                        "Stock_State": "current",
                        "Stock_Match": "exact_code",
                        "Brand": "AccuStandard",
                        "Code": "CP-CA-RS-02-LONG-REFERENCE-CODE",
                        "Size": "1 mL",
                        "Cas": "75-21-8",
                        "Stock_Expiry_Label": "31/12/2027",
                        "Stock_Price": "475.000 ₫",
                        "Stock_Note": "Kho lạnh tầng 2 — xác nhận với thủ kho trước khi giữ hàng.",
                    }
                ],
            },
            {
                "product_id": 102,
                "Name": "Ethylene oxide certified reference solution",
                "Code": "EO-CRM-100",
                "Cas": "75-21-8",
                "Brand": "Cayman Chemical",
                "Size": "100 mg",
                "Unit_Price": "1,820,000",
                "Note": "Chỉ sử dụng cho mục đích phân tích.",
                "Compliance_Status": "Cần rà soát hồ sơ",
                "Compliance_Note": "Ghi chú quản lý dài để kiểm tra wrap: đối chiếu giấy phép, mục đích sử dụng và thông tin đơn vị nhận trước khi xuất.",
                "Compliance_Css": "regulatory-color-amber",
                "Stock_Options": [
                    {
                        "Stock_Quantity": 3,
                        "Stock_State": "near_expiry",
                        "Stock_Match": "same_cas",
                        "Stock_Warning": "Sắp hết hạn — cần xác nhận trước khi báo khách",
                        "Brand": "Cayman Chemical",
                        "Code": "EO-CRM-100-ALT",
                        "Size": "100 mg",
                        "Cas": "75-21-8",
                        "Stock_Expiry_Label": "15/11/2026",
                        "Stock_Price": "1.700.000 ₫",
                    }
                ],
            },
            {
                "product_id": None,
                "Name": "Stock-only archived catalog identity with an exceptionally long descriptive product name",
                "Code": "STOCK-ONLY-75-21-8",
                "Cas": "75-21-8",
                "Brand": "TRC Pharmaceuticals",
                "Size": "5 x 1 mL",
                "Unit_Price": "",
                "Note": "",
                "Compliance": "",
                "Compliance_Note": "",
                "Result_Kind": "stock_only",
                "Stock_Options": [
                    {
                        "Stock_Quantity": 1,
                        "Stock_State": "missing",
                        "Stock_Match": "name",
                        "Stock_Warning": "Không có hạn sử dụng",
                        "Brand": "TRC Pharmaceuticals",
                        "Code": "STOCK-ONLY-75-21-8",
                        "Size": "5 x 1 mL",
                        "Cas": "75-21-8",
                        "Stock_Expiry_Label": "",
                    }
                ],
            },
        ],
        stock_truncated=False,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5069)
    args = parser.parse_args()
    app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False)
