"""Read-only exact-scope confirmation for retained single-record admin tools."""
import hashlib
import json
from flask import current_app, session
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from brand_gateway import load_brand_gateway


def scope(cur, kind, row):
    text=lambda k:str(row.get(k) or '').strip()
    if kind=='product':
        brand=text('brand');code=text('code');cas=text('cas');name=text('name');size=text('size')
        if not brand or not (code or cas or name):raise ValueError('Cần brand và mã, CAS hoặc tên sản phẩm.')
        resolution=load_brand_gateway(cur).resolve(brand)
        if not resolution.is_valid:raise ValueError('Brand không hợp lệ.')
        clauses=['upper(trim(brand))=upper(trim(%s))'];params=[resolution.canonical_brand]
        if resolution.source_brand != resolution.canonical_brand:
            clauses.append('upper(trim(source_brand))=upper(trim(%s))');params.append(resolution.source_brand)
        field='code' if code else 'cas' if cas else 'name'
        clauses.append(f'upper(trim({field}))=upper(trim(%s))');params.append(text(field))
        if cas and not code and name:
            clauses.append('upper(trim(name))=upper(trim(%s))');params.append(name)
        if size:
            clauses.append("upper(trim(COALESCE(size,'')))=upper(trim(%s))");params.append(size)
        table='products';label=f'{resolution.canonical_brand} / nguồn: {resolution.source_brand if resolution.source_brand != resolution.canonical_brand else "tất cả nguồn"} / {field}: {text(field)}' + (f' / {size}' if size else '')
    elif kind=='rule':
        if not text('rule_type') or not text('match_field') or not text('match_value'):raise ValueError('Cần loại quy tắc, trường và giá trị khớp.')
        clauses=['rule_type=%s','match_field=%s','upper(trim(match_value))=upper(trim(%s))'];params=[text('rule_type').upper(),text('match_field').lower(),text('match_value')]
        table='regulatory_rules';label=' / '.join(params)
    else:raise ValueError('Loại dữ liệu không hợp lệ.')
    cur.execute(f"SELECT id,xmin::text FROM {table} WHERE {' AND '.join(clauses)} ORDER BY id LIMIT 1001",params)
    targets=cur.fetchall()
    if not targets:raise ValueError('Không tìm thấy dòng để xóa.')
    if len(targets)>1000:raise ValueError('Phạm vi quá 1.000 dòng. Hãy thu hẹp điều kiện xóa.')
    digest=hashlib.sha256(json.dumps([kind,params,targets],ensure_ascii=False).encode()).hexdigest()
    return table,label,targets,digest


def serializer():return URLSafeTimedSerializer(current_app.secret_key,salt='admin-import-delete-v1')


def preview(cur,kind,row):
    _,label,targets,digest=scope(cur,kind,row)
    token=serializer().dumps({'actor':session.get('user_id'),'auth':session.get('auth_version'),'digest':digest})
    return {'ok':True,'label':label,'count':len(targets),'confirmation':token}


def apply(cur,kind,row):
    try:claim=serializer().loads(row.get('confirmation',''),max_age=300)
    except (BadSignature,SignatureExpired):raise ValueError('Cần xem trước phạm vi xóa rồi xác nhận lại.') from None
    table,label,targets,digest=scope(cur,kind,row)
    if claim != {'actor':session.get('user_id'),'auth':session.get('auth_version'),'digest':digest}:
        raise ValueError('Phạm vi xóa đã thay đổi. Hãy xem trước lại.')
    cur.execute(f'DELETE FROM {table} WHERE id=ANY(%s)',([r[0] for r in targets],))
    return label,cur.rowcount
