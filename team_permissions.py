"""Team capability registry and request-boundary enforcement (Phase 6C0.1).

Grants are explicit, extensible keys. New keys require an intentional grant;
missing/invalid policy never means full access. Only migration 021 seeds the
legacy set. LOCAL, GOOGLE and legacy staff share the same live team lookup.
"""
from flask import abort, g, has_request_context, jsonify, request, session
import session_security

FEATURES = {
    'SEARCH': 'Search', 'CHECK_LICENSE': 'Check License', 'FIND_CODE': 'Find Code',
    'ADVANCED_SEARCH': 'Advanced Search', 'SEARCH_BY_CAS': 'Tìm bằng CAS',
    'QUICK_QUOTE': 'Quick Quote', 'COPY': 'Copy', 'EXPORT': 'Export',
}
FIELDS = {
    'VIEW_NAME': ('Name', 'Name'), 'VIEW_CODE': ('Code', 'Code'),
    'VIEW_CAS': ('Cas', 'CAS'), 'VIEW_BRAND': ('Brand', 'Brand'),
    'VIEW_SIZE': ('Size', 'Size'), 'VIEW_PRICE': ('Unit_Price', 'Unit Price'),
    'VIEW_NOTE': ('Note', 'Note'), 'VIEW_COMPLIANCE': ('Compliance', 'Tình trạng quản lý'),
    'VIEW_COMPLIANCE_NOTE': ('Compliance_Note', 'Ghi chú quản lý'),
}
REGISTRY = {**FEATURES, **{key: value[1] for key, value in FIELDS.items()}}
LEGACY_PERMISSIONS = tuple(REGISTRY)
DEPENDENCIES = {
    'QUICK_QUOTE': ('VIEW_PRICE',),
    'CHECK_LICENSE': ('SEARCH_BY_CAS', 'VIEW_COMPLIANCE'),
    'ADVANCED_SEARCH': ('SEARCH_BY_CAS',),
    # Export eligibility depends on live compliance state. Without this field
    # grant, a product-specific success/failure becomes a compliance oracle.
    'EXPORT': ('VIEW_COMPLIANCE',),
}
ROUTES = {
    'home': (), 'search_products': ('SEARCH',),
    'check_cas': ('CHECK_LICENSE',), 'check_cas_batch': ('CHECK_LICENSE',),
    'find_code_batch': ('FIND_CODE',),
    'advanced_search': ('ADVANCED_SEARCH',),
    'advanced_search_options': ('ADVANCED_SEARCH',),
    'quick_quote': ('QUICK_QUOTE',),
    'quote_assistant_match': ('QUICK_QUOTE',),
    'quote_assistant_preflight': ('QUICK_QUOTE',),
    'quote_assistant_request_file_analyze': ('QUICK_QUOTE',),
    'quote_assistant_request_file_parse': ('QUICK_QUOTE',),
    'quote_assistant_workbook_template': ('QUICK_QUOTE',),
    'quote_assistant_workbook_export': ('QUICK_QUOTE', 'EXPORT'),
    'results_quote_export': ('EXPORT',),
    'results_copy': ('COPY',), 'results_export': ('EXPORT',),
}
# All wire aliases, including derived values and nested quote metadata.
ALIASES = {
    'VIEW_NAME': {'name', 'requested_name'},
    'VIEW_CODE': {'code', 'requested_code'},
    'VIEW_CAS': {'cas', 'requested_cas', 'resolved_cas', 'resolved_cas_u', 'cas_values', 'cas_list'},
    'VIEW_BRAND': {'brand', 'brands', 'selected_brand', 'matched_brand', 'effective_brand_policy', 'fallback_path'},
    'VIEW_SIZE': {'size', 'sizes', 'size_parse', 'size_pairs'},
    'VIEW_PRICE': {'price', 'ship', 'unit_price', 'unit_price_value', 'unit_price_available',
                   'currency_rate_status', 'currency_rate_message', 'total', 'total_price'},
    'VIEW_NOTE': {'note'},
    'VIEW_COMPLIANCE': {'compliance', 'compliance_status', 'compliance_css', 'compliance_source',
                        'compliance_export_policy', 'compliance_status_id', 'compliance_stable_key',
                        'compliance_color',
                        'warning', 'warning_type', 'warnings', 'export_policy', 'message'},
    'VIEW_COMPLIANCE_NOTE': {'compliance_note'},
}


def validate_permissions(values):
    if not isinstance(values, (list, tuple)) or any(not isinstance(k, str) or k not in REGISTRY for k in values):
        raise ValueError('Quyền không hợp lệ. Vui lòng tải lại trang và chọn lại.')
    return sorted(set(values))


def current_permissions():
    if not has_request_context():
        return frozenset()
    if not session.get('authenticated') or request.endpoint in session_security.PRE_AUTH_EXEMPT_ENDPOINTS:
        return frozenset()
    if session.get('is_admin'):
        return frozenset(REGISTRY)
    if 'team_permissions' not in g:
        try:
            conn = session_security.get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT permission_keys FROM teams WHERE id = %s AND lifecycle_status = 'ACTIVE'",
                                (session.get('team_id'),))
                    row = cur.fetchone()
                if row is None:
                    raise ValueError('missing team')
                g.team_permissions = frozenset(validate_permissions(row[0]))
            finally:
                conn.close()
        except Exception:
            abort(503, description='Không thể xác thực quyền truy cập. Vui lòng thử lại.')
    return g.team_permissions


def allows(key, grants):
    return key in grants and all(dep in grants for dep in DEPENDENCIES.get(key, ()))


def can(key):
    return allows(key, current_permissions())


def redact(value, grants=None):
    """Drop protected keys recursively before serialization, including aliases."""
    grants = current_permissions() if grants is None else grants
    denied = set().union(*(aliases for key, aliases in ALIASES.items() if key not in grants))
    if 'VIEW_COMPLIANCE' not in grants or 'VIEW_COMPLIANCE_NOTE' not in grants:
        denied.add('compliance_combined')
    def walk(item):
        if isinstance(item, list):
            return [walk(v) for v in item]
        if isinstance(item, dict):
            out = {k: walk(v) for k, v in item.items() if k.lower() not in denied}
            if 'VIEW_COMPLIANCE' not in grants:
                for key in ('reason', 'reason_code', 'ineligible_reason'):
                    if isinstance(out.get(key), str) and 'COMPLIANCE' in out[key]:
                        out[key] = 'NOT_ELIGIBLE'
            return out
        return item
    return walk(value)


def enforce():
    required = ROUTES.get(request.endpoint)
    if required is None:
        return
    if not session.get('authenticated'):
        if request.endpoint in {'home', 'quick_quote'}:
            return  # existing HTML login redirect
        return jsonify(error='Chưa đăng nhập.'), 401
    if any(not can(key) for key in required):
        return jsonify(error='Team không có quyền thực hiện chức năng này.'), 403
    # Load on home too, so empty grants render an accessible landing page.
    current_permissions()



def init_app(app):
    app.before_request(enforce)
    app.context_processor(lambda: {'can': can, 'permission_fields': FIELDS,
                                   'permission_keys': sorted(current_permissions())})

    @app.after_request
    def protect_response(response):
        if request.endpoint in ROUTES and session.get('authenticated'):
            response.headers['Cache-Control'] = 'no-store, private'
            if response.is_json and not session.get('is_admin') and response.status_code < 400:
                response.set_data(app.json.dumps(redact(response.get_json())))
        return response
