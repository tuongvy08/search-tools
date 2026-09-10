"""Shared streaming product workbook engine. No Flask and no connection ownership.

Caller owns the transaction. Temporary SQL tables bound Python memory; product
mutations require the same advisory lock as quick edits. Web keeps raw staged
rows durably; CLI uses the same parser, plan and apply functions.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from pathlib import PurePosixPath
from xml.etree import ElementTree

from openpyxl import load_workbook
from psycopg2.extras import Json, execute_values

from brand_gateway import load_brand_gateway, preview_import_rows_brands, register_and_resolve_import_rows, inspect_replace_by_brand_scopes
from product_import_manual import validate_product_import_rows, parse_manual_compliance_row, parse_preparation_type_row


class ImportProblem(ValueError):
    """Only controlled, Vietnamese messages may reach UI/logs."""


def limit(name, default):
    return max(1, int(os.environ.get('IMPORT_' + name, default)))


COLUMNS = ('name', 'code', 'cas', 'brand', 'size', 'ship', 'price', 'note', 'source_brand',
           'manual_compliance', 'manual_compliance_note', 'preparation_type')


def inspect_workbook(path):
    """No extraction. Reject macros, external references and excessive expansion.

    All XML parts are scanned before openpyxl parses shared strings/styles (which
    read_only still materializes). Expansion limits therefore protect that path.
    """
    if os.path.getsize(path) > limit('MAX_BYTES', 128 * 1024**2):
        raise ImportProblem('Tệp vượt quá giới hạn dung lượng.')
    try:
        with zipfile.ZipFile(path) as z:
            entries = z.infolist()
            if len(entries) > limit('ZIP_ENTRIES', 2048):
                raise ImportProblem('Tệp có quá nhiều thành phần ZIP.')
            names = [x.filename for x in entries]
            if len(set(names)) != len(names) or '[Content_Types].xml' not in names or 'xl/workbook.xml' not in names:
                raise ImportProblem('Tệp không phải workbook XLSX hợp lệ.')
            if sum(x.file_size for x in entries) > limit('ZIP_BYTES', 768 * 1024**2):
                raise ImportProblem('Dung lượng giải nén vượt giới hạn.')
            content_info = z.getinfo('[Content_Types].xml')
            if content_info.file_size > 1024**2:
                raise ImportProblem('Danh mục thành phần workbook quá lớn.')
            content_xml = z.read(content_info)
            if any(marker in content_xml.upper() for marker in (b'<!DOCTYPE', b'<!ENTITY', b'\x00')):
                raise ImportProblem('XML workbook không an toàn.')
            types = ElementTree.fromstring(content_xml)
            worksheet_parts = set()
            metadata_parts = set()
            for entry in types:
                content_type = entry.attrib.get('ContentType','').lower()
                part = entry.attrib.get('PartName','').lstrip('/')
                if content_type == 'application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml':
                    if not re.fullmatch(r'xl/worksheets/sheet[0-9]+\.xml', part):
                        raise ImportProblem('Vị trí trang tính không được hỗ trợ.')
                    worksheet_parts.add(part)
                else:
                    metadata_parts.add(part)
                if 'sheet.main+xml' in content_type and part != 'xl/workbook.xml':
                    raise ImportProblem('Vị trí workbook không được hỗ trợ.')
            if worksheet_parts & metadata_parts:
                raise ImportProblem('Loại thành phần workbook mâu thuẫn.')
            if sum(x.file_size for x in entries if x.filename not in worksheet_parts) > limit('METADATA_BYTES', 64 * 1024**2):
                raise ImportProblem('Tổng định dạng và chuỗi workbook quá lớn.')
            for item in entries:
                name = item.filename
                if (name.startswith('/') or '..' in PurePosixPath(name).parts or '\\' in name
                        or item.flag_bits & 1 or 'vbaproject' in name.lower()
                        or name.startswith(('xl/externalLinks/', 'xl/embeddings/'))):
                    raise ImportProblem('Tệp có macro, liên kết ngoài hoặc thành phần không an toàn.')
                if item.file_size > limit('ZIP_RATIO', 250) * max(1, item.compress_size):
                    raise ImportProblem('Tỷ lệ nén ZIP vượt giới hạn an toàn.')
                if not name.endswith(('.xml', '.rels')):
                    raise ImportProblem('Workbook chỉ được chứa XML; hãy bỏ hình ảnh và phần nhúng trước khi nhập.')
                if name not in worksheet_parts and item.file_size > limit('METADATA_BYTES', 64 * 1024**2):
                    raise ImportProblem('Bảng chuỗi hoặc định dạng Excel quá lớn; lưu lại workbook đơn giản.')
                with z.open(item) as source:
                    tail = b''
                    while True:
                        chunk = source.read(65536)
                        if not chunk:
                            break
                        scan = (tail + chunk).upper()
                        if b'<!DOCTYPE' in scan or b'<!ENTITY' in scan or b'\x00' in scan:
                            raise ImportProblem('XML workbook không an toàn.')
                        tail = scan[-32:]
                if name.endswith('.rels'):
                    if item.file_size > 1024**2:
                        raise ImportProblem('Danh sách liên kết workbook quá lớn.')
                    root = ElementTree.fromstring(z.read(item))
                    if any(e.attrib.get('TargetMode', '').lower() == 'external' for e in root):
                        raise ImportProblem('Hãy bỏ liên kết ngoài trước khi nhập workbook.')
            types = ElementTree.fromstring(z.read('[Content_Types].xml'))
            for entry in types:
                content_type = entry.attrib.get('ContentType','').lower()
                part = entry.attrib.get('PartName','')
                if ('sharedstrings' in content_type and part != '/xl/sharedStrings.xml') or ('styles+xml' in content_type and part != '/xl/styles.xml'):
                    raise ImportProblem('Vị trí bảng chuỗi/định dạng workbook không được hỗ trợ.')
                if 'macroenabled' in content_type:
                    raise ImportProblem('Không hỗ trợ workbook có macro.')
    except ImportProblem:
        raise
    except Exception:
        raise ImportProblem('Không đọc được XLSX. Hãy lưu lại bằng Excel Workbook (.xlsx).') from None


def workbook_rows(path, progress=lambda *_: None):
    inspect_workbook(path)
    wb = None
    try:
        wb = load_workbook(path, read_only=True, data_only=False, keep_links=False)
        if len(wb.worksheets) != 1:
            raise ImportProblem('Chỉ nhập workbook có một trang tính để tránh bỏ sót dữ liệu.')
        ws = wb.worksheets[0]
        ws.reset_dimensions()  # Do not trust attacker-controlled cached dimensions.
        rows = ws.iter_rows()
        first = next(rows, ())
        headers = [str(c.value or '').strip().lower() for c in first]
        if len(headers) > 32 or len(headers) != len(set(headers)) or 'brand' not in headers:
            raise ImportProblem('Dòng tiêu đề cần cột brand, tối đa 32 cột và không được trùng tên.')
        allowed = set(COLUMNS) - {'manual_compliance', 'manual_compliance_note'} | {'compliance', 'compliance_note'}
        if any(h not in allowed for h in headers):
            raise ImportProblem('Cột không được hỗ trợ. Hãy dùng tên cột trong file mẫu.')
        try:
            validate_product_import_rows([], set(headers))
        except ValueError:
            raise ImportProblem('Cần cả Compliance và Compliance_Note, hoặc bỏ cả hai.') from None
        count = 0
        for line, cells in enumerate(rows, 2):
            if line > limit('MAX_ROWS', 1000000) + 1:
                raise ImportProblem('Workbook vượt giới hạn số dòng.')
            if len(cells) > len(headers):
                raise ImportProblem(f'Dòng {line}: có ô nằm ngoài tiêu đề.')
            if any(c.data_type in ('f', 'e') for c in cells):
                raise ImportProblem(f'Dòng {line}: hãy thay công thức hoặc lỗi Excel bằng giá trị trước khi nhập.')
            values = [str(c.value).strip() if c.value is not None else '' for c in cells]
            if any(len(v) > limit('CELL_CHARS', 4000) or '\x00' in v for v in values):
                raise ImportProblem(f'Dòng {line}: ô quá dài hoặc có ký tự không hợp lệ.')
            if not any(values):
                progress(line - 1, 1)
                continue
            row = dict(zip(headers, values))
            if not row.get('brand') or len(row['brand']) > 180 or len(row.get('source_brand', '')) > 180:
                raise ImportProblem(f'Dòng {line}: brand/nguồn brand trống hoặc quá dài.')
            try:
                validate_product_import_rows([row], set(headers))
                if 'compliance' in headers:
                    row['manual_compliance'], row['manual_compliance_note'] = parse_manual_compliance_row(row)
                if 'preparation_type' in headers:
                    row['preparation_type'] = parse_preparation_type_row(row)
            except ValueError:
                raise ImportProblem(f'Dòng {line}: Compliance, Compliance_Note hoặc Preparation_Type không hợp lệ.') from None
            row['_manual'] = 'compliance' in headers
            row['_preparation'] = 'preparation_type' in headers
            count += 1
            yield line, row
        if not count:
            raise ImportProblem('Workbook không có dòng sản phẩm.')
    except ImportProblem:
        raise
    except Exception:
        raise ImportProblem('Không đọc được dữ liệu XLSX. Hãy kiểm tra file và thử lại.') from None
    finally:
        if wb is not None:
            wb.close()


def create_stage(cur):
    cur.execute('DROP TABLE IF EXISTS pg_temp.import_stage')
    cur.execute('CREATE TEMP TABLE import_stage (n INTEGER PRIMARY KEY, data JSONB NOT NULL) ON COMMIT DROP')


def fill_stage(cur, rows, progress=lambda *_: None):
    batch = []
    count = 0
    for n, row in rows:
        batch.append((n, Json(row)))
        if len(batch) >= limit('CHUNK_ROWS', 2000):
            execute_values(cur, 'INSERT INTO import_stage VALUES %s', batch)
            count += len(batch)
            progress(count)
            batch = []
    if batch:
        execute_values(cur, 'INSERT INTO import_stage VALUES %s', batch)
        count += len(batch)
        progress(count)
    return count


def resolve_stage(cur, apply=False):
    cur.execute("SELECT DISTINCT data->>'brand', COALESCE(data->>'source_brand','') FROM import_stage ORDER BY 1,2 LIMIT %s", (limit('MAX_SCOPES', 2000) + 1,))
    pairs = cur.fetchall()
    if len(pairs) > limit('MAX_SCOPES', 2000):
        raise ImportProblem('Workbook có quá nhiều phạm vi brand/nguồn brand.')
    raw = [{'brand': b, 'source_brand': s} for b, s in pairs]
    gateway = load_brand_gateway(cur)
    if not gateway.table_exists:
        raise ImportProblem('Chưa cài đặt Brand Master. Liên hệ quản trị triển khai migration.')
    if apply:
        try:
            resolved, new = register_and_resolve_import_rows(cur, raw)
        except ValueError:
            raise ImportProblem('Brand Master đã thay đổi hoặc brand bị khóa. Hãy xem trước lại.') from None
    else:
        resolved, errors, new = preview_import_rows_brands(raw, gateway)
        if errors:
            raise ImportProblem('Brand không hợp lệ.')
    cur.execute('CREATE TEMP TABLE import_brand_map (raw_brand TEXT, raw_source TEXT, brand TEXT, source TEXT) ON COMMIT DROP')
    execute_values(cur, 'INSERT INTO import_brand_map VALUES %s', [(b, s, r['brand'], r['source_brand']) for (b,s),r in zip(pairs,resolved)])
    cur.execute("""CREATE TEMP TABLE import_resolved ON COMMIT DROP AS
        SELECT s.n, s.data || jsonb_build_object('brand',m.brand,'source_brand',m.source) AS data
        FROM import_stage s JOIN import_brand_map m ON s.data->>'brand'=m.raw_brand
            AND COALESCE(s.data->>'source_brand','')=m.raw_source""")
    cur.execute('CREATE UNIQUE INDEX ON import_resolved(n)')
    cur.execute("CREATE INDEX ON import_resolved(upper(trim(data->>'code')),upper(trim(data->>'brand')))")
    cur.execute('ANALYZE import_resolved')
    return resolved, new


def catalog_fingerprint(cur):
    # Full affected brands, including xmin, catch inserts/deletes/edits and
    # same-count replacement. Read server-side in batches; never string_agg.
    cur.execute("DECLARE import_fingerprint NO SCROLL CURSOR FOR SELECT p.id,p.xmin::text,to_jsonb(p)::text FROM products p WHERE upper(trim(p.brand)) IN (SELECT upper(trim(brand)) FROM import_brand_map) ORDER BY p.id")
    digest = hashlib.sha256()
    while True:
        cur.execute('FETCH 2000 FROM import_fingerprint')
        rows = cur.fetchall()
        if not rows:
            break
        for row in rows:
            digest.update(json.dumps(row, ensure_ascii=False).encode())
    cur.execute('CLOSE import_fingerprint')
    cur.execute('SELECT raw_brand,raw_source,brand,source FROM import_brand_map ORDER BY 1,2')
    digest.update(json.dumps(cur.fetchall(), ensure_ascii=False).encode())
    # Manual compliance intent is status-ID based. Bind preview validity to the
    # whole status catalog so rename + old-label reuse cannot redirect apply.
    cur.execute(
        """SELECT id,stable_key,label,priority,export_policy,updated_at::text
           FROM regulatory_statuses ORDER BY id"""
    )
    digest.update(json.dumps(cur.fetchall(), ensure_ascii=False).encode())
    return digest.hexdigest()


def build_plan(cur, mode, apply=False):
    scopes, new = resolve_stage(cur, apply)
    cur.execute("""SELECT DISTINCT data->>'manual_compliance'
                   FROM import_resolved
                   WHERE COALESCE((data->>'_manual')::boolean,false)
                     AND NULLIF(trim(data->>'manual_compliance'),'') IS NOT NULL
                     AND NOT EXISTS (
                       SELECT 1 FROM regulatory_statuses s
                       WHERE upper(trim(s.label))=upper(trim(data->>'manual_compliance'))
                     ) LIMIT 1""")
    unknown_manual = cur.fetchone()
    if unknown_manual:
        raise ImportProblem(
            f"Tình trạng quản lý thủ công không có trong danh mục: {unknown_manual[0]}."
        )
    cur.execute(
        """UPDATE import_resolved r
           SET data=r.data || jsonb_build_object('_manual_status_id',s.id)
           FROM regulatory_statuses s
           WHERE COALESCE((r.data->>'_manual')::boolean,false)
             AND NULLIF(trim(r.data->>'manual_compliance'),'') IS NOT NULL
             AND upper(trim(s.label))=upper(trim(r.data->>'manual_compliance'))"""
    )
    fingerprint = catalog_fingerprint(cur)
    deleted = 0
    scope_details = []
    cur.execute('CREATE TEMP TABLE import_targets (id BIGINT PRIMARY KEY) ON COMMIT DROP')
    if mode == 'replace_by_brand':
        gateway = load_brand_gateway(cur)
        mapping, errors, _ = inspect_replace_by_brand_scopes(cur, scopes, gateway)
        if errors:
            raise ImportProblem('Brand có nhiều nguồn catalog. Cần chỉ rõ source_brand hoặc alias trong workbook.')
        for brand, sources in sorted(mapping.items()):
            # Same exact source predicate as Brand Gateway target resolver.
            cur.execute("INSERT INTO import_targets SELECT id FROM products WHERE upper(trim(brand))=upper(trim(%s)) AND upper(trim(source_brand))=ANY(%s)", (brand, [s.upper().strip() for s in sources]))
            scope_details.append({'brand': brand, 'sources': sorted(sources), 'count': cur.rowcount})
        cur.execute('SELECT count(*) FROM import_targets')
        deleted = cur.fetchone()[0]
    elif mode == 'replace_all':  # CLI compatibility only; never accepted by web.
        cur.execute('INSERT INTO import_targets SELECT id FROM products')
        deleted = cur.rowcount
    elif mode not in ('upsert','append'):
        raise ImportProblem('Chế độ nhập không hợp lệ.')

    cur.execute('CREATE TEMP TABLE import_matches (n INTEGER PRIMARY KEY, id BIGINT) ON COMMIT DROP')
    if mode == 'upsert':
        # Reject duplicate incoming identities, including blank-size wildcard
        # overlaps. No arbitrary last-row-wins when several rows target one id.
        cur.execute("""SELECT 1 FROM import_resolved WHERE COALESCE(data->>'code','')<>''
            GROUP BY upper(trim(data->>'brand')),upper(trim(data->>'code')),upper(trim(data->>'source_brand')),upper(trim(COALESCE(data->>'size','')))
            HAVING count(*)>1 LIMIT 1""")
        if cur.fetchone():
            raise ImportProblem('Workbook trùng mã/brand/nguồn/quy cách. Gộp các dòng trùng trước khi upsert.')
        cur.execute("""CREATE TEMP TABLE import_candidates ON COMMIT DROP AS
          WITH candidates AS (
            SELECT s.n,p.id,upper(trim(p.source_brand))=upper(trim(s.data->>'source_brand')) AS src,
              COALESCE(s.data->>'size','')<>'' AND upper(trim(COALESCE(p.size,'')))=upper(trim(s.data->>'size')) AS sz
            FROM import_resolved s JOIN products p ON upper(trim(p.code))=upper(trim(s.data->>'code'))
              AND upper(trim(p.brand))=upper(trim(s.data->>'brand')) WHERE COALESCE(s.data->>'code','')<>''
          ), source_filtered AS (
            SELECT *,count(*) OVER(PARTITION BY n) AS total, bool_or(src) OVER(PARTITION BY n) AS has_src FROM candidates
          ), size_candidates AS (
            SELECT *,bool_or(sz) OVER(PARTITION BY n) AS has_sz FROM source_filtered WHERE total=1 OR NOT has_src OR src
          ) SELECT n,id FROM size_candidates WHERE NOT has_sz OR sz""")
        cur.execute('SELECT 1 FROM import_candidates GROUP BY n HAVING count(*)>1 LIMIT 1')
        if cur.fetchone():
            raise ImportProblem('Có mã sản phẩm mơ hồ trong database. Hãy chỉ rõ nguồn brand và quy cách; chưa ghi dòng nào.')
        cur.execute('INSERT INTO import_matches SELECT n,id FROM import_candidates')
        cur.execute('SELECT 1 FROM import_matches GROUP BY id HAVING count(*)>1 LIMIT 1')
        if cur.fetchone():
            raise ImportProblem('Nhiều dòng trong file cùng cập nhật một sản phẩm. Hãy gộp dòng trước khi nhập.')
    cur.execute('SELECT count(*) FROM import_resolved')
    count = cur.fetchone()[0]
    cur.execute('SELECT count(*) FROM import_matches')
    updated = cur.fetchone()[0]
    cur.execute('SELECT data FROM import_resolved ORDER BY n LIMIT 10')
    sample = [{k:v for k,v in r[0].items() if not k.startswith('_')} for r in cur.fetchall()]
    return {'fingerprint': fingerprint, 'row_count': count, 'inserted': count-updated, 'updated': updated,
            'deleted': deleted, 'scopes': scope_details, 'new_brands': [b['name'] for b in new], 'sample': sample}


def apply_plan(cur, plan, expected=None, progress=lambda *_: None):
    if expected is not None and plan['fingerprint'] != expected['fingerprint']:
        raise ImportProblem('Dữ liệu, Brand Gateway hoặc danh mục tình trạng đã thay đổi từ lúc xem trước. Hãy xem trước lại rồi xác nhận.')
    # Preserve optional controls on replacements only when exact identity is
    # unambiguous. Never copy another catalog/size's compliance override.
    cur.execute("""CREATE TEMP TABLE import_preserved ON COMMIT DROP AS
        SELECT upper(trim(p.brand)) b,upper(trim(p.code)) c,upper(trim(p.source_brand)) s,
               upper(trim(COALESCE(p.size,''))) z,min(p.manual_compliance) mc,
               min(p.manual_compliance_status_id) msid,
               min(p.manual_compliance_note) mn,min(p.preparation_type) pt
        FROM products p JOIN import_targets t ON t.id=p.id WHERE COALESCE(trim(p.code),'')<>''
        GROUP BY 1,2,3,4 HAVING count(*)=1""")
    cur.execute('DELETE FROM products p USING import_targets t WHERE p.id=t.id')
    progress(0)
    assignments = ','.join(f"{c}=s.data->>'{c}'" for c in COLUMNS[:9])
    cur.execute(f"""UPDATE products p SET {assignments},
        manual_compliance=CASE WHEN (s.data->>'_manual')::boolean THEN s.data->>'manual_compliance' ELSE p.manual_compliance END,
        manual_compliance_status_id=CASE WHEN (s.data->>'_manual')::boolean
            THEN NULLIF(s.data->>'_manual_status_id','')::bigint ELSE p.manual_compliance_status_id END,
        manual_compliance_note=CASE WHEN (s.data->>'_manual')::boolean THEN s.data->>'manual_compliance_note' ELSE p.manual_compliance_note END,
        preparation_type=CASE WHEN (s.data->>'_preparation')::boolean THEN s.data->>'preparation_type' ELSE p.preparation_type END
        FROM import_resolved s JOIN import_matches m ON m.n=s.n WHERE p.id=m.id""")
    progress(plan['updated'])
    # Bounded inserts by row-number range, one atomic transaction overall.
    cur.execute('SELECT min(n),max(n) FROM import_resolved')
    lo, hi = cur.fetchone()
    for start in range(lo, hi+1, limit('CHUNK_ROWS', 2000)):
        expressions = ','.join(f"s.data->>'{c}'" for c in COLUMNS[:9])
        cur.execute(f"""INSERT INTO products ({','.join(COLUMNS[:10])},manual_compliance_status_id,{','.join(COLUMNS[10:])})
            SELECT {expressions},
            CASE WHEN (s.data->>'_manual')::boolean THEN s.data->>'manual_compliance' ELSE old.mc END,
            CASE WHEN (s.data->>'_manual')::boolean THEN NULLIF(s.data->>'_manual_status_id','')::bigint ELSE old.msid END,
            CASE WHEN (s.data->>'_manual')::boolean THEN s.data->>'manual_compliance_note' ELSE old.mn END,
            CASE WHEN (s.data->>'_preparation')::boolean THEN s.data->>'preparation_type' ELSE old.pt END
            FROM import_resolved s LEFT JOIN import_matches m ON m.n=s.n
            LEFT JOIN import_preserved old ON old.b=upper(trim(s.data->>'brand')) AND old.c=upper(trim(s.data->>'code'))
              AND old.s=upper(trim(s.data->>'source_brand')) AND old.z=upper(trim(COALESCE(s.data->>'size','')))
            WHERE m.n IS NULL AND s.n>=%s AND s.n<%s""", (start, start+limit('CHUNK_ROWS',2000)))
        progress(min(plan['row_count'], start+limit('CHUNK_ROWS',2000)-2))
    return plan
