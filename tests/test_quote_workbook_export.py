import io
import json
import os
import re
import unittest
import zipfile
from unittest import mock
from unittest.mock import patch
from xml.etree import ElementTree as ET

import search
import quote_workbook_export as qwe
from auth_test_helpers import start_auth_db_patch


NS = {"m": qwe.NS_MAIN}
NS_X14AC = "http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac"
NS_XR = "http://schemas.microsoft.com/office/spreadsheetml/2014/revision"
NS_XR2 = "http://schemas.microsoft.com/office/spreadsheetml/2015/revision2"
NS_XR3 = "http://schemas.microsoft.com/office/spreadsheetml/2016/revision3"
REAL_TEMPLATE_FIXTURE = os.environ.get("QUOTE_TEMPLATE_FIXTURE")


def _cell_ref(cell):
    return cell.attrib.get("r")


def _cell(root, ref):
    for cell in root.findall(".//m:c", NS):
        if _cell_ref(cell) == ref:
            return cell
    return None


def _text(cell):
    if cell is None:
        return ""
    if cell.attrib.get("t") == "inlineStr":
        return "".join(t.text or "" for t in cell.findall(".//m:t", NS))
    v = cell.find("m:v", NS)
    return "" if v is None else (v.text or "")


def _formula(cell):
    f = cell.find("m:f", NS) if cell is not None else None
    return "" if f is None else (f.text or "")


def _sheet_xml(raw, path="xl/worksheets/custom_bg.xml"):
    with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
        return ET.fromstring(zf.read(path))


def _zip_entries(raw):
    with zipfile.ZipFile(io.BytesIO(raw), "r") as zf:
        return {name: zf.read(name) for name in zf.namelist()}


def _root_start_tag(data):
    return re.search(rb"<[A-Za-z_][^>]*>", data).group(0).decode("utf-8")


def _declared_prefixes(data):
    return dict(re.findall(r'xmlns:([^\s=]+)="([^"]*)"', _root_start_tag(data)))


def _ignorable_tokens(data):
    match = re.search(r'\bmc:Ignorable="([^"]*)"', _root_start_tag(data))
    return match.group(1).split() if match else []


def _inline_cell(col, row, value, style=1):
    return (
        f'<c r="{col}{row}" s="{style}" t="inlineStr">'
        f"<is><t>{value}</t></is></c>"
    )


def _number_cell(col, row, value, style=1):
    return f'<c r="{col}{row}" s="{style}"><v>{value}</v></c>'


def _formula_cell(col, row, formula, style=1):
    return f'<c r="{col}{row}" s="{style}"><f>{formula}</f><v>0</v></c>'


def make_workbook(
    *,
    sheet_name="BG",
    headers=None,
    product_rows=9,
    macro=False,
    vat_formula=None,
    grand_total_formula=None,
    calc_chain=True,
    ignorable="x14ac xr xr2 xr3",
):
    headers = headers or qwe.BG_HEADERS
    rows = ['<row r="1"><c r="A1" t="inlineStr"><is><t>Logo anchor area</t></is></c></row>']
    header_cells = "".join(_inline_cell(col, 16, value, 2) for col, value in headers.items())
    rows.append(f'<row r="16">{header_cells}</row>')
    for row_num in range(17, 17 + product_rows):
        cells = [
            _number_cell("A", row_num, row_num - 16, 3),
            _inline_cell("B", row_num, f"OLD {row_num}", 4),
            _inline_cell("C", row_num, f"C{row_num}", 5),
            _inline_cell("D", row_num, f"CAS{row_num}", 6),
            _inline_cell("E", row_num, f"Brand{row_num}", 7),
            _inline_cell("F", row_num, "g", 8),
            _formula_cell("G", row_num, f"A{row_num}+1", 9),
            _formula_cell("J", row_num, f"P{row_num}*2", 10),
            _inline_cell("M", row_num, f"Note {row_num}", 11),
            _inline_cell("N", row_num, f"Compliance {row_num}", 12),
            _number_cell("P", row_num, 123, 13),
        ]
        rows.append(
            f'<row r="{row_num}" ht="18" customHeight="1" x14ac:dyDescent="0.25">{"".join(cells)}</row>'
        )
    total_row = 17 + product_rows
    vat_expr = vat_formula if vat_formula is not None else f"J{total_row}*0.08"
    grand_expr = grand_total_formula if grand_total_formula is not None else f"SUM(J{total_row}:J{total_row + 1})"
    rows.extend(
        [
            f'<row r="{total_row}">{_inline_cell("I", total_row, "Tổng giá", 20)}{_formula_cell("J", total_row, "SUM(J17:J25)", 21)}</row>',
            f'<row r="{total_row + 1}">{_inline_cell("I", total_row + 1, "VAT", 20)}{_formula_cell("J", total_row + 1, vat_expr, 21)}</row>',
            f'<row r="{total_row + 2}">{_inline_cell("I", total_row + 2, "Tổng gồm VAT", 20)}{_formula_cell("J", total_row + 2, grand_expr, 21)}</row>',
            f'<row r="{total_row + 4}">{_formula_cell("L", total_row + 4, f"J{total_row}", 31)}{_inline_cell("M", total_row + 4, "Footer terms", 30)}</row>',
        ]
    )
    ignorable_attr = f' mc:Ignorable="{ignorable}"' if ignorable else ""
    sheet_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="{qwe.NS_MAIN}" xmlns:r="{qwe.NS_REL}" xmlns:mc="{qwe.NS_MARKUP_COMPAT}"{ignorable_attr} xmlns:x14ac="{NS_X14AC}" xmlns:xr="{NS_XR}" xmlns:xr2="{NS_XR2}" xmlns:xr3="{NS_XR3}" xr:uid="{{C0CE2866-4CA0-459A-8860-962040CFE29E}}">
  <dimension ref="A1:P{total_row + 4}"/>
  <sheetData>{''.join(rows)}</sheetData>
  <mergeCells count="2">
    <mergeCell ref="A1:B1"/>
    <mergeCell ref="M{total_row + 4}:N{total_row + 4}"/>
  </mergeCells>
  <drawing r:id="rId1"/>
  <legacyDrawing r:id="rId2"/>
  <pageSetup r:id="rId3"/>
</worksheet>'''.encode()
    files = {
        "[Content_Types].xml": f'''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="tmp" ContentType="application/octet-stream"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/custom_bg.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  {f'<Override PartName="/xl/calcChain.xml" ContentType="{qwe.CALC_CHAIN_CONTENT_TYPE}"/>' if calc_chain else ""}
  {"<Override PartName='/xl/vbaProject.bin' ContentType='application/vnd.ms-office.vbaProject'/>" if macro else ""}
</Types>'''.encode(),
        "_rels/.rels": f'''<Relationships xmlns="{qwe.NS_PKG_REL}">
  <Relationship Id="rIdWorkbook" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''.encode(),
        "xl/workbook.xml": f'''<workbook xmlns="{qwe.NS_MAIN}" xmlns:r="{qwe.NS_REL}" xmlns:mc="{qwe.NS_MARKUP_COMPAT}" mc:Ignorable="x15 xr2" xmlns:x15="http://schemas.microsoft.com/office/spreadsheetml/2010/11/main" xmlns:xr2="{NS_XR2}">
  <sheets><sheet name="{sheet_name}" sheetId="1" r:id="rIdBg"/></sheets>
  <extLst><ext uri="{{140A7094-0E35-4892-8432-C4D2E57EDEB5}}"><x15:workbookPr chartTrackingRefBase="1"/></ext></extLst>
</workbook>'''.encode(),
        "xl/_rels/workbook.xml.rels": f'''<Relationships xmlns="{qwe.NS_PKG_REL}">
  <Relationship Id="rIdBg" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/custom_bg.xml"/>
  <Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
  {f'<Relationship Id="rIdCalc" Type="{qwe.CALC_CHAIN_REL_TYPE}" Target="calcChain.xml"/>' if calc_chain else ""}
</Relationships>'''.encode(),
        "xl/worksheets/custom_bg.xml": sheet_xml,
        "xl/worksheets/_rels/custom_bg.xml.rels": f'''<Relationships xmlns="{qwe.NS_PKG_REL}">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" Target="../drawings/drawing1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/vmlDrawing" Target="../drawings/vmlDrawing1.vml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/printerSettings" Target="../printerSettings/printerSettings1.bin"/>
</Relationships>'''.encode(),
        "xl/drawings/drawing1.xml": b"<drawing>fake drawing anchor</drawing>",
        "xl/drawings/vmlDrawing1.vml": b"<xml>fake vml</xml>",
        "xl/printerSettings/printerSettings1.bin": b"fake printer settings",
        "xl/media/image1.tmp": b"fake logo bytes",
        "xl/styles.xml": b"<styleSheet/>",
    }
    if calc_chain:
        files["xl/calcChain.xml"] = (
            f'<calcChain xmlns="{qwe.NS_MAIN}"><c r="J26" i="1"/><c r="J27" i="1"/></calcChain>'
        ).encode()
    if macro:
        files["xl/vbaProject.bin"] = b"macro"
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return bio.getvalue()


def product(idx, **overrides):
    data = {
        "Name": f"Name {idx}",
        "Code": f"Code {idx}",
        "Cas": f"Cas {idx}",
        "Brand": f"Brand {idx}",
        "Size": f"{idx}g",
        "Note": f"Note {idx}",
        "Compliance_Combined": "Được bán | ok",
        "Unit_Price_Value": 1000 * idx,
    }
    data.update(overrides)
    return data


class WorkbookOoxmlExportTests(unittest.TestCase):
    def test_maps_cells_and_numeric_price_from_row_17(self):
        out = qwe.export_quick_quote_workbook(make_workbook(), [product(1)])
        root = _sheet_xml(out)
        self.assertEqual(_text(_cell(root, "B17")), "Name 1")
        self.assertEqual(_text(_cell(root, "C17")), "Code 1")
        self.assertEqual(_text(_cell(root, "D17")), "Cas 1")
        self.assertEqual(_text(_cell(root, "E17")), "Brand 1")
        self.assertEqual(_text(_cell(root, "F17")), "1g")
        self.assertEqual(_text(_cell(root, "M17")), "Note 1")
        self.assertEqual(_text(_cell(root, "N17")), "Được bán | ok")
        self.assertIsNone(_cell(root, "P17").attrib.get("t"))
        self.assertEqual(_text(_cell(root, "P17")), "1000")

    def test_compliance_combined_variants(self):
        items = [
            product(1, Compliance_Combined="Được bán | note"),
            product(2, Compliance_Combined="Được bán"),
            product(3, Compliance_Combined="note only"),
            product(4, Compliance_Combined=""),
        ]
        out = qwe.export_quick_quote_workbook(make_workbook(), items)
        root = _sheet_xml(out)
        self.assertEqual(_text(_cell(root, "N17")), "Được bán | note")
        self.assertEqual(_text(_cell(root, "N18")), "Được bán")
        self.assertEqual(_text(_cell(root, "N19")), "note only")
        self.assertEqual(_text(_cell(root, "N20")), "")

    def test_formula_like_text_is_inline_string(self):
        items = [
            product(1, Name="=SUM(1,1)"),
            product(2, Name="+plus"),
            product(3, Name="-minus"),
            product(4, Name="@handle"),
        ]
        out = qwe.export_quick_quote_workbook(make_workbook(), items)
        root = _sheet_xml(out)
        for row, expected in [(17, "=SUM(1,1)"), (18, "+plus"), (19, "-minus"), (20, "@handle")]:
            cell = _cell(root, f"B{row}")
            self.assertEqual(cell.attrib.get("t"), "inlineStr")
            self.assertEqual(_formula(cell), "")
            self.assertEqual(_text(cell), expected)

    def test_placeholder_row_renders_blank_price_and_note_text(self):
        """Phase 4A: a placeholder (no product_id) row reuses the same product
        writer, so style/row-height match product rows, price is left blank
        (not 0) so it contributes 0 via the existing per-row formula, and
        requested Name/Code/Cas starting with =/+/-/@ stay literal inlineStr
        text (no formula injection)."""
        items = [
            product(1),
            {
                "Name": "=SUM(1,1)",
                "Code": "+PLUS",
                "Cas": "-MINUS",
                "Brand": "",
                "Size": "",
                "Note": "",
                "Compliance_Combined": "Không tìm thấy",
                "Unit_Price_Value": None,
                "STT": "2",
            },
        ]
        out = qwe.export_quick_quote_workbook(make_workbook(), items)
        root = _sheet_xml(out)
        self.assertEqual(_cell(root, "B18").attrib.get("t"), "inlineStr")
        self.assertEqual(_formula(_cell(root, "B18")), "")
        self.assertEqual(_text(_cell(root, "B18")), "=SUM(1,1)")
        self.assertEqual(_text(_cell(root, "C18")), "+PLUS")
        self.assertEqual(_text(_cell(root, "D18")), "-MINUS")
        self.assertEqual(_text(_cell(root, "E18")), "")
        self.assertEqual(_text(_cell(root, "N18")), "Không tìm thấy")
        self.assertEqual(_text(_cell(root, "P18")), "")
        self.assertIsNone(_cell(root, "P18").attrib.get("t"))
        # placeholder row keeps the template's own row style/height (row 18 = style 3/ht 18, same as a product row)
        self.assertEqual(_cell(root, "A18").attrib.get("s"), _cell(root, "A17").attrib.get("s"))
        # per-row total formula (P*qty) is untouched; blank P evaluates to 0
        self.assertEqual(_formula(_cell(root, "J18")), "P18*2")
        # placeholder row still lands inside the SUM total range
        self.assertEqual(_formula(_cell(root, "J26")), "SUM(J17:J18)")

    def test_under_capacity_keeps_footer_rows_and_clears_only_mapped_cells(self):
        out = qwe.export_quick_quote_workbook(make_workbook(), [product(1), product(2)])
        root = _sheet_xml(out)
        self.assertEqual(_text(_cell(root, "I26")), "Tổng giá")
        self.assertEqual(_text(_cell(root, "B19")), "")
        self.assertEqual(_text(_cell(root, "P19")), "")
        self.assertEqual(_formula(_cell(root, "G19")), "A19+1")

    def test_exact_capacity_keeps_total_row_26(self):
        out = qwe.export_quick_quote_workbook(make_workbook(), [product(i) for i in range(1, 10)])
        root = _sheet_xml(out)
        self.assertEqual(_text(_cell(root, "A25")), "9")
        self.assertEqual(_text(_cell(root, "I26")), "Tổng giá")
        self.assertEqual(_formula(_cell(root, "J26")), "SUM(J17:J25)")
        self.assertEqual(_formula(_cell(root, "J27")), "J26*0.08")
        self.assertEqual(_formula(_cell(root, "J28")), "SUM(J26:J27)")

    def test_under_capacity_preserves_template_vat_rate(self):
        out = qwe.export_quick_quote_workbook(make_workbook(), [product(1), product(2)])
        root = _sheet_xml(out)
        self.assertEqual(_formula(_cell(root, "J26")), "SUM(J17:J18)")
        self.assertEqual(_formula(_cell(root, "J27")), "J26*0.08")
        self.assertEqual(_formula(_cell(root, "J28")), "SUM(J26:J27)")

    def test_over_capacity_inserts_rows_copies_style_numbering_and_shifts_footer(self):
        out = qwe.export_quick_quote_workbook(make_workbook(), [product(i) for i in range(1, 11)])
        root = _sheet_xml(out)
        self.assertEqual(_text(_cell(root, "A26")), "10")
        self.assertEqual(_cell(root, "A26").attrib.get("s"), _cell(root, "A25").attrib.get("s"))
        self.assertEqual(_text(_cell(root, "I27")), "Tổng giá")
        self.assertEqual(_text(_cell(root, "M31")), "Footer terms")
        self.assertEqual(_formula(_cell(root, "L31")), "J27")
        self.assertEqual(_formula(_cell(root, "G26")), "")

    def test_formulas_update_after_shift(self):
        out = qwe.export_quick_quote_workbook(make_workbook(), [product(i) for i in range(1, 12)])
        root = _sheet_xml(out)
        self.assertEqual(_formula(_cell(root, "J28")), "SUM(J17:J27)")
        self.assertEqual(_formula(_cell(root, "J29")), "J28*0.08")
        self.assertEqual(_formula(_cell(root, "J30")), "SUM(J28:J29)")

    def test_preserves_custom_vat_rate_from_template(self):
        raw = make_workbook(vat_formula="J26*0.05", grand_total_formula="SUM(J26:J27)")
        out = qwe.export_quick_quote_workbook(raw, [product(i) for i in range(1, 11)])
        root = _sheet_xml(out)
        self.assertEqual(_formula(_cell(root, "J27")), "SUM(J17:J26)")
        self.assertEqual(_formula(_cell(root, "J28")), "J27*0.05")
        self.assertEqual(_formula(_cell(root, "J29")), "SUM(J27:J28)")

    def test_merged_ranges_below_total_shift(self):
        out = qwe.export_quick_quote_workbook(make_workbook(), [product(i) for i in range(1, 11)])
        root = _sheet_xml(out)
        refs = [mc.attrib["ref"] for mc in root.findall(".//m:mergeCell", NS)]
        self.assertIn("A1:B1", refs)
        self.assertIn("M31:N31", refs)

    def test_non_sheet_entries_preserved_byte_identical(self):
        raw = make_workbook()
        out = qwe.export_quick_quote_workbook(raw, [product(1)])
        before = _zip_entries(raw)
        after = _zip_entries(out)
        rewritten = {
            "xl/worksheets/custom_bg.xml",
            "xl/workbook.xml",
            "xl/_rels/workbook.xml.rels",
            "[Content_Types].xml",
            "xl/calcChain.xml",
        }
        for name, data in before.items():
            if name in rewritten:
                continue
            self.assertEqual(after[name], data, name)
        self.assertEqual(after["xl/media/image1.tmp"], b"fake logo bytes")
        self.assertEqual(after["xl/drawings/drawing1.xml"], b"<drawing>fake drawing anchor</drawing>")
        self.assertEqual(after["xl/drawings/vmlDrawing1.vml"], b"<xml>fake vml</xml>")
        self.assertEqual(after["xl/printerSettings/printerSettings1.bin"], b"fake printer settings")
        self.assertEqual(after["xl/styles.xml"], b"<styleSheet/>")

    def test_worksheet_keeps_original_namespace_prefixes_and_declares_ignorable(self):
        out = qwe.export_quick_quote_workbook(make_workbook(), [product(1)])
        sheet = _zip_entries(out)["xl/worksheets/custom_bg.xml"]
        declared = _declared_prefixes(sheet)
        self.assertEqual(declared.get("x14ac"), NS_X14AC)
        self.assertEqual(declared.get("xr"), NS_XR)
        self.assertEqual(declared.get("xr2"), NS_XR2)
        self.assertEqual(declared.get("xr3"), NS_XR3)
        self.assertEqual(declared.get("mc"), qwe.NS_MARKUP_COMPAT)
        self.assertEqual(declared.get("r"), qwe.NS_REL)
        self.assertEqual(_ignorable_tokens(sheet), ["x14ac", "xr", "xr2", "xr3"])
        self.assertEqual([t for t in _ignorable_tokens(sheet) if t not in declared], [])
        self.assertEqual([p for p in declared if re.fullmatch(r"ns\d+", p)], [])
        self.assertIn('xr:uid="{C0CE2866-4CA0-459A-8860-962040CFE29E}"', _root_start_tag(sheet))
        self.assertIn('x14ac:dyDescent="0.25"', sheet.decode("utf-8"))
        self.assertIn(f'xmlns="{qwe.NS_MAIN}"', _root_start_tag(sheet))

    def test_ignorable_tokens_without_declaration_are_sanitized(self):
        raw = make_workbook(ignorable="x14ac xr xr2 xr3 x16r2")
        out = qwe.export_quick_quote_workbook(raw, [product(1)])
        sheet = _zip_entries(out)["xl/worksheets/custom_bg.xml"]
        declared = _declared_prefixes(sheet)
        self.assertEqual(_ignorable_tokens(sheet), ["x14ac", "xr", "xr2", "xr3"])
        self.assertNotIn("x16r2", declared)

    def test_export_fails_when_ignorable_prefix_would_be_undeclared(self):
        with patch.object(qwe, "_restore_namespace_declarations", lambda root, declarations: None):
            with self.assertRaisesRegex(qwe.WorkbookExportError, "mc:Ignorable"):
                qwe.export_quick_quote_workbook(make_workbook(), [product(1)])

    def test_calc_chain_part_relationship_and_content_type_removed(self):
        out = qwe.export_quick_quote_workbook(make_workbook(), [product(1)])
        after = _zip_entries(out)
        self.assertNotIn("xl/calcChain.xml", after)
        rels = after["xl/_rels/workbook.xml.rels"].decode("utf-8")
        self.assertNotIn("calcChain", rels)
        self.assertIn("worksheets/custom_bg.xml", rels)
        self.assertIn("styles.xml", rels)
        content_types = after["[Content_Types].xml"].decode("utf-8")
        self.assertNotIn("calcChain", content_types)
        self.assertIn("/xl/worksheets/custom_bg.xml", content_types)
        self.assertIn("/xl/styles.xml", content_types)

    def test_workbook_requests_full_recalculation_and_keeps_extensions(self):
        out = qwe.export_quick_quote_workbook(make_workbook(), [product(1)])
        workbook = _zip_entries(out)["xl/workbook.xml"]
        root = ET.fromstring(workbook)
        calc_pr = root.find(f"{{{qwe.NS_MAIN}}}calcPr")
        self.assertIsNotNone(calc_pr)
        self.assertEqual(calc_pr.attrib.get("calcMode"), "auto")
        self.assertEqual(calc_pr.attrib.get("fullCalcOnLoad"), "1")
        self.assertEqual(calc_pr.attrib.get("forceFullCalc"), "1")
        children = [child.tag for child in root]
        self.assertLess(children.index(f"{{{qwe.NS_MAIN}}}calcPr"), children.index(f"{{{qwe.NS_MAIN}}}extLst"))
        self.assertIn("chartTrackingRefBase", workbook.decode("utf-8"))
        declared = _declared_prefixes(workbook)
        self.assertEqual([t for t in _ignorable_tokens(workbook) if t not in declared], [])

    def test_existing_calc_pr_attributes_are_updated(self):
        raw = make_workbook()
        entries = _zip_entries(raw)
        entries["xl/workbook.xml"] = entries["xl/workbook.xml"].replace(
            b"<extLst>", b'<calcPr calcId="191029" fullCalcOnLoad="0"/><extLst>'
        )
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name, data in entries.items():
                zf.writestr(name, data)
        out = qwe.export_quick_quote_workbook(bio.getvalue(), [product(1)])
        root = ET.fromstring(_zip_entries(out)["xl/workbook.xml"])
        calc_prs = root.findall(f"{{{qwe.NS_MAIN}}}calcPr")
        self.assertEqual(len(calc_prs), 1)
        self.assertEqual(calc_prs[0].attrib.get("calcId"), "191029")
        self.assertEqual(calc_prs[0].attrib.get("fullCalcOnLoad"), "1")

    def test_workbook_without_calc_chain_still_exports(self):
        out = qwe.export_quick_quote_workbook(make_workbook(calc_chain=False), [product(1)])
        after = _zip_entries(out)
        self.assertNotIn("xl/calcChain.xml", after)
        self.assertEqual(_text(_cell(_sheet_xml(out), "B17")), "Name 1")

    def test_rejects_wrong_sheet_header_malformed_macro_oversize_and_zip_bomb(self):
        with self.assertRaisesRegex(qwe.WorkbookExportError, "sheet BG"):
            qwe.inspect_bg_template(make_workbook(sheet_name="Other"))
        bad_headers = dict(qwe.BG_HEADERS)
        bad_headers["B"] = "Wrong"
        with self.assertRaisesRegex(qwe.WorkbookExportError, "Header"):
            qwe.inspect_bg_template(make_workbook(headers=bad_headers))
        with self.assertRaisesRegex(qwe.WorkbookExportError, "OOXML"):
            qwe.inspect_bg_template(b"PK bad")
        with self.assertRaisesRegex(qwe.WorkbookExportError, "macro"):
            qwe.inspect_bg_template(make_workbook(macro=True))
        with patch.object(qwe, "MAX_XLSX_BYTES", 64):
            with self.assertRaisesRegex(qwe.WorkbookExportError, "quá lớn"):
                qwe.inspect_bg_template(make_workbook())
        with patch.object(qwe, "MAX_XLSX_UNCOMPRESSED_BYTES", 32):
            with self.assertRaisesRegex(qwe.WorkbookExportError, "zip bomb"):
                qwe.inspect_bg_template(make_workbook())

    def test_rejects_missing_footer_formulas(self):
        raw = make_workbook()
        root = _sheet_xml(raw)
        vat_cell = _cell(root, "J27")
        for child in list(vat_cell):
            if child.tag.endswith("f"):
                vat_cell.remove(child)
        vat_cell.append(ET.Element(f"{{{qwe.NS_MAIN}}}v"))
        vat_cell.find(f"{{{qwe.NS_MAIN}}}v").text = "100"
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name, data in _zip_entries(raw).items():
                if name == "xl/worksheets/custom_bg.xml":
                    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
                zf.writestr(name, data)
        with self.assertRaisesRegex(qwe.WorkbookExportError, "Footer sheet BG"):
            qwe.inspect_bg_template(bio.getvalue())


@unittest.skipUnless(
    REAL_TEMPLATE_FIXTURE and os.path.exists(REAL_TEMPLATE_FIXTURE),
    "QUOTE_TEMPLATE_FIXTURE env var not configured or file not found",
)
class RealTemplateExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(REAL_TEMPLATE_FIXTURE, "rb") as fh:
            cls.raw = fh.read()
        cls.before = _zip_entries(cls.raw)

    def setUp(self):
        # Phase 5D2A: `enforce_session_validity` now needs a `user_id` +
        # `auth_version` matching an ACTIVE `app_users` row for any
        # authenticated session; stub that check with an in-memory fake
        # (never touches real Postgres) so this class's one HTTP-level test
        # keeps exercising the real export code path end to end.
        start_auth_db_patch(self)

    def _export(self, count):
        return qwe.export_quick_quote_workbook(self.raw, [product(i) for i in range(1, count + 1)])

    def test_namespaces_and_calc_chain_fixed_for_one_full_and_over_capacity(self):
        total_row = qwe.inspect_bg_template(self.raw).total_row
        for count in (1, 9, 12):
            with self.subTest(count=count):
                after = _zip_entries(self._export(count))
                sheet = after["xl/worksheets/sheet1.xml"]
                declared = _declared_prefixes(sheet)
                tokens = _ignorable_tokens(sheet)
                self.assertEqual(tokens, ["x14ac", "xr", "xr2", "xr3"])
                self.assertEqual([t for t in tokens if t not in declared], [])
                self.assertEqual(declared.get("xr"), NS_XR)
                self.assertEqual(declared.get("x14ac"), NS_X14AC)
                self.assertEqual([p for p in declared if re.fullmatch(r"ns\d+", p)], [])
                self.assertIn("xr:uid=", _root_start_tag(sheet))

                self.assertNotIn("xl/calcChain.xml", after)
                self.assertNotIn("calcChain", after["xl/_rels/workbook.xml.rels"].decode("utf-8"))
                self.assertNotIn("calcChain", after["[Content_Types].xml"].decode("utf-8"))
                calc_pr = ET.fromstring(after["xl/workbook.xml"]).find(f"{{{qwe.NS_MAIN}}}calcPr")
                self.assertEqual(calc_pr.attrib.get("fullCalcOnLoad"), "1")
                self.assertEqual(calc_pr.attrib.get("forceFullCalc"), "1")
                self.assertEqual(calc_pr.attrib.get("calcMode"), "auto")

                for name in (
                    "xl/media/image1.tmp",
                    "xl/drawings/drawing1.xml",
                    "xl/drawings/_rels/drawing1.xml.rels",
                    "xl/printerSettings/printerSettings1.bin",
                    "xl/styles.xml",
                    "xl/theme/theme1.xml",
                    "xl/sharedStrings.xml",
                    "xl/worksheets/_rels/sheet1.xml.rels",
                ):
                    self.assertEqual(after[name], self.before[name], name)

                root = ET.fromstring(sheet)
                shifted = max(0, count - (total_row - qwe.PRODUCT_START_ROW))
                footer_row = total_row + shifted
                self.assertEqual(_text(_cell(root, "B17")), "Name 1")
                self.assertEqual(_text(_cell(root, f"B{16 + count}")), f"Name {count}")
                self.assertEqual(_text(_cell(root, f"A{16 + count}")), str(count))
                self.assertEqual(_text(_cell(root, f"P{16 + count}")), str(1000 * count))
                self.assertEqual(
                    _formula(_cell(root, f"J{footer_row}")), f"SUM(J17:J{16 + count})"
                )
                self.assertTrue(_formula(_cell(root, f"J{footer_row + 1}")))
                self.assertTrue(_formula(_cell(root, f"J{footer_row + 2}")))

    def test_source_template_not_modified(self):
        with open(REAL_TEMPLATE_FIXTURE, "rb") as fh:
            self.assertEqual(fh.read(), self.raw)

    def test_export_api_with_active_template_returns_downloadable_workbook(self):
        """End-to-end: no workbook upload, real active template bytes, real exporter."""
        search.app.testing = True
        client = search.app.test_client()
        with client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["user_id"] = 1
            sess["auth_version"] = 1
            sess["is_admin"] = False
            sess["team_id"] = 7
        with patch.object(
            search,
            "_get_active_quote_template",
            return_value={
                "id": 1,
                "filename": "From_BG_V2.xlsx",
                "profile_version": "BG_V1",
                "content_size": len(self.raw),
                "created_at": "2026-08-27T10:00:00+00:00",
                "activated_at": "2026-08-27T10:00:01+00:00",
                "content": self.raw,
            },
        ), patch.object(
            search, "_quote_export_products", return_value=[product(1), product(2)]
        ), patch.object(search, "get_connection", return_value=FakeConnection([])):
            response = client.post(
                "/api/quote-assistant/workbook/export",
                data={"selections": json.dumps([{"product_id": 42}, {"product_id": 43}])},
                content_type="multipart/form-data",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], qwe.XLSX_MIME)
        self.assertIn("From_BG_V2_draft.xlsx", response.headers["Content-Disposition"])
        body = response.get_data()
        self.assertTrue(body.startswith(b"PK\x03\x04"), "response must be a ZIP/OOXML payload")

        after = _zip_entries(body)
        self.assertNotIn("xl/calcChain.xml", after)
        sheet = after["xl/worksheets/sheet1.xml"]
        declared = _declared_prefixes(sheet)
        self.assertEqual([t for t in _ignorable_tokens(sheet) if t not in declared], [])
        root = ET.fromstring(sheet)
        self.assertEqual(_text(_cell(root, "B17")), "Name 1")
        self.assertEqual(_text(_cell(root, "B18")), "Name 2")


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def execute(self, query, params=None):
        self.conn.queries.append(query)
        if "SELECT brand, rate FROM exchange_rates" in query:
            self.rows = []
            return
        self.rows = self.conn.export_rows

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows):
        self.export_rows = rows
        self.queries = []
        self.closed = False

    def cursor(self, *args, **kwargs):
        return FakeCursor(self)

    def close(self):
        self.closed = True


class QuoteWorkbookExportApiTests(unittest.TestCase):
    def setUp(self):
        start_auth_db_patch(self)
        # Phase 6A-UAT gap fix: this class's non-admin cases use a plain
        # `team_id=123` session and mock `search.get_connection` for the
        # product/export queries only. Fix1's real IP/team-policy
        # middleware now issues a real `SELECT ip_policy FROM teams ...`
        # for any non-admin session, which is unrelated to what this file
        # tests -- disable it explicitly (same pattern as
        # test_admin_teams.py) instead of letting it fail with a stray 503.
        self._disable_ip_patch = mock.patch.dict(
            "os.environ", {"DISABLE_IP_ALLOWLIST": "1"}
        )
        self._disable_ip_patch.start()
        self.addCleanup(self._disable_ip_patch.stop)

    def _post(self, rows, selections, *, authenticated=True, is_admin=True, team_id=1):
        fake_conn = FakeConnection(rows)
        search.app.testing = True
        with patch("search.get_connection", return_value=fake_conn), patch(
            "search.export_quick_quote_workbook", return_value=b"exported-xlsx"
        ) as export_mock:
            with search.app.test_client() as client:
                if authenticated:
                    with client.session_transaction() as sess:
                        sess["authenticated"] = True
                        sess["user_id"] = 1
                        sess["auth_version"] = 1
                        sess["is_admin"] = is_admin
                        if team_id is not None:
                            sess["team_id"] = team_id
                response = client.post(
                    "/api/quote-assistant/workbook/export",
                    data={
                        "workbook": (io.BytesIO(make_workbook()), "quote.xlsx"),
                        "selections": json.dumps(selections, ensure_ascii=False),
                    },
                    content_type="multipart/form-data",
                )
        return response, fake_conn, export_mock

    def test_api_auth_visibility_invalid_candidates_order_duplicates_and_no_n_plus_1(self):
        ok_row = (1, 42, "First", "C1", "CAS1", "Brand", "1g", "10", "100", "note", "NEAT", "Được bán", "ok", True, None, None)
        dup_row = (2, 42, "First", "C1", "CAS1", "Brand", "1g", "10", "100", "note", "NEAT", "Được bán", "ok", True, None, None)
        response, conn, export_mock = self._post(
            [ok_row, dup_row],
            [{"product_id": 42}, {"product_id": 42}],
            is_admin=False,
            team_id=123,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Disposition"].count("quote_draft.xlsx"), 1)
        products = export_mock.call_args.args[1]
        self.assertEqual([p["product_id"] for p in products], [42, 42])
        self.assertEqual([p["Name"] for p in products], ["First", "First"])
        self.assertEqual(products[0]["Compliance_Combined"], "Được bán | ok")
        product_queries = [q for q in conn.queries if "WITH input AS" in q]
        self.assertEqual(len(product_queries), 1)
        self.assertIn("team_brands", product_queries[0])

        unauth, _conn, _mock = self._post([], [{"product_id": 42}], authenticated=False)
        self.assertEqual(unauth.status_code, 401)
        no_team, _conn, _mock = self._post([], [{"product_id": 42}], is_admin=False, team_id=None)
        self.assertEqual(no_team.status_code, 403)

        missing, _conn, _mock = self._post([], [{"product_id": 99}])
        self.assertEqual(missing.status_code, 400)
        self.assertIn("không visible", missing.get_json()["error"])

        blocked_row = (1, 7, "Blocked", "B", "CAS", "Brand", "1g", "1", "100", "", "NEAT", "CẤM NHẬP", "", True, None, None)
        blocked, _conn, _mock = self._post([blocked_row], [{"product_id": 7}])
        self.assertEqual(blocked.status_code, 400)
        self.assertIn("compliance", blocked.get_json()["error"])

        zero_row = (1, 8, "Zero", "Z", "CAS", "Brand", "1g", "1", "0", "", "NEAT", "Được bán", "", True, None, None)
        zero, _conn, _mock = self._post([zero_row], [{"product_id": 8}])
        self.assertEqual(zero.status_code, 400)
        self.assertIn("Unit_Price", zero.get_json()["error"])


def _export_items_payload(items):
    return {"export_items": items}


class QuoteWorkbookExportV2ApiTests(unittest.TestCase):
    """Phase 1 export_items v2: identity-preserving, re-fetched, sorted, STT labels."""

    def setUp(self):
        start_auth_db_patch(self)

    def _post_items(self, rows, items, *, authenticated=True, is_admin=True, team_id=1, include_selections=False):
        fake_conn = FakeConnection(rows)
        search.app.testing = True
        with patch("search.get_connection", return_value=fake_conn), patch(
            "search.export_quick_quote_workbook", return_value=b"exported-xlsx"
        ) as export_mock:
            with search.app.test_client() as client:
                if authenticated:
                    with client.session_transaction() as sess:
                        sess["authenticated"] = True
                        sess["user_id"] = 1
                        sess["auth_version"] = 1
                        sess["is_admin"] = is_admin
                        if team_id is not None:
                            sess["team_id"] = team_id
                data = {
                    "workbook": (io.BytesIO(make_workbook()), "quote.xlsx"),
                    "export_items": json.dumps(items, ensure_ascii=False),
                }
                if include_selections:
                    data["selections"] = json.dumps(
                        [{"product_id": ln["product_id"] for it in items for ln in it["lines"]}],
                        ensure_ascii=False,
                    )
                response = client.post(
                    "/api/quote-assistant/workbook/export",
                    data=data,
                    content_type="multipart/form-data",
                )
        return response, fake_conn, export_mock

    def _row(self, ord_, product_id, name="P"):
        return (ord_, product_id, name, f"C{product_id}", f"CAS{product_id}", "Brand", "1g", "10", "100", "note", "NEAT", "Được bán", "ok", True, None, None)

    def test_export_items_re_fetches_and_sorts_by_request_then_selection_order(self):
        rows = [self._row(1, 101, "A"), self._row(2, 205, "B"), self._row(3, 307, "C")]
        items = [
            {"request_id": "r2", "request_order": 2, "source_row": 12,
             "requested_name": "req2", "requested_code": "C2", "requested_cas": "CAS2",
             "lines": [{"product_id": 205, "selection_order": 1}]},
            {"request_id": "r1", "request_order": 1, "source_row": 7,
             "requested_name": "req1", "requested_code": "C1", "requested_cas": "CAS1",
             "lines": [{"product_id": 101, "selection_order": 1}]},
        ]
        response, conn, export_mock = self._post_items(rows, items)
        self.assertEqual(response.status_code, 200)
        products = export_mock.call_args.args[1]
        self.assertEqual([p["product_id"] for p in products], [101, 205])
        self.assertEqual([p["STT"] for p in products], ["1", "2"])
        self.assertEqual([p["request_id"] for p in products], ["r1", "r2"])
        self.assertEqual(products[0]["requested_name"], "req1")
        # single bulk query, no n+1
        product_queries = [q for q in conn.queries if "WITH input AS" in q]
        self.assertEqual(len(product_queries), 1)

    def test_export_items_multi_line_uses_sub_ordinal_stt(self):
        rows = [
            self._row(1, 101, "A"),
            self._row(2, 205, "B"),
            self._row(3, 307, "C"),
        ]
        items = [
            {"request_id": "r5", "request_order": 5, "source_row": None,
             "requested_name": "req5", "requested_code": "", "requested_cas": "",
             "lines": [
                 {"product_id": 205, "selection_order": 2},
                 {"product_id": 101, "selection_order": 1},
                 {"product_id": 307, "selection_order": 3},
             ]},
        ]
        response, _conn, export_mock = self._post_items(rows, items)
        self.assertEqual(response.status_code, 200)
        products = export_mock.call_args.args[1]
        # selection_order drives line order: 101 (ord 1), 205 (ord 2), 307 (ord 3)
        self.assertEqual([p["product_id"] for p in products], [101, 205, 307])
        # first line uses request_order (5), subsequent lines use .1, .2
        self.assertEqual([p["STT"] for p in products], ["5", "5.1", "5.2"])

    def test_export_items_two_requests_stt_sequence(self):
        rows = [
            self._row(1, 101, "A"),
            self._row(2, 205, "B"),
            self._row(3, 307, "C"),
        ]
        # Request 1 has 2 products, Request 2 has 1 product -> 1, 1.1, 2
        items = [
            {"request_id": "r1", "request_order": 1, "source_row": 1,
             "requested_name": "req1", "requested_code": "", "requested_cas": "",
             "lines": [
                 {"product_id": 101, "selection_order": 1},
                 {"product_id": 205, "selection_order": 2},
             ]},
            {"request_id": "r2", "request_order": 2, "source_row": 2,
             "requested_name": "req2", "requested_code": "", "requested_cas": "",
             "lines": [
                 {"product_id": 307, "selection_order": 1},
             ]},
        ]
        response, _conn, export_mock = self._post_items(rows, items)
        self.assertEqual(response.status_code, 200)
        products = export_mock.call_args.args[1]
        self.assertEqual([p["product_id"] for p in products], [101, 205, 307])
        self.assertEqual([p["STT"] for p in products], ["1", "1.1", "2"])

    def test_export_items_single_line_stt_is_plain_request_order(self):
        rows = [self._row(1, 101)]
        items = [
            {"request_id": "r5", "request_order": 5, "source_row": None,
             "requested_name": "", "requested_code": "", "requested_cas": "",
             "lines": [{"product_id": 101, "selection_order": 1}]},
        ]
        response, _conn, export_mock = self._post_items(rows, items)
        self.assertEqual(response.status_code, 200)
        products = export_mock.call_args.args[1]
        self.assertEqual(products[0]["STT"], "5")

    def test_export_items_preserves_duplicate_product_across_requests(self):
        rows = [self._row(1, 42, "Shared"), self._row(2, 42, "Shared")]
        items = [
            {"request_id": "r1", "request_order": 1, "source_row": None,
             "requested_name": "", "requested_code": "", "requested_cas": "",
             "lines": [{"product_id": 42, "selection_order": 1}]},
            {"request_id": "r2", "request_order": 2, "source_row": None,
             "requested_name": "", "requested_code": "", "requested_cas": "",
             "lines": [{"product_id": 42, "selection_order": 1}]},
        ]
        response, _conn, export_mock = self._post_items(rows, items)
        self.assertEqual(response.status_code, 200)
        products = export_mock.call_args.args[1]
        self.assertEqual([p["product_id"] for p in products], [42, 42])
        self.assertEqual([p["STT"] for p in products], ["1", "2"])
        self.assertEqual([p["request_id"] for p in products], ["r1", "r2"])

    def test_export_items_rejects_duplicate_request_id_and_bad_lines(self):
        rows = [self._row(1, 101)]
        dup = [
            {"request_id": "dup", "request_order": 1, "lines": [{"product_id": 101, "selection_order": 1}]},
            {"request_id": "dup", "request_order": 2, "lines": [{"product_id": 101, "selection_order": 1}]},
        ]
        response, _conn, _mock = self._post_items(rows, dup)
        self.assertEqual(response.status_code, 400)

        bad_product = [
            {"request_id": "x", "request_order": 1, "lines": [
                {"product_id": 101, "selection_order": 1},
                {"product_id": 101, "selection_order": 2},
            ]},
        ]
        response, _conn, _mock = self._post_items(rows, bad_product)
        self.assertEqual(response.status_code, 400)

        missing_id = [
            {"request_order": 1, "lines": [{"product_id": 101, "selection_order": 1}]},
        ]
        response, _conn, _mock = self._post_items(rows, missing_id)
        self.assertEqual(response.status_code, 400)

    def test_export_items_rejects_invisible_product(self):
        # no row returned for product 999 → invisible
        response, _conn, _mock = self._post_items(
            [],
            [{"request_id": "r1", "request_order": 1, "lines": [{"product_id": 999, "selection_order": 1}]}],
        )
        self.assertEqual(response.status_code, 400)

    # ── Phase 4A: placeholder lines preserve request order ──────────────

    def test_export_items_unresolved_placeholder_preserves_stt_1_2_3(self):
        """Selected -> unresolved -> selected must keep STT 1, 2, 3 (request 2
        is not dropped and request 3 is not promoted to STT 2)."""
        # flat_lines assigns ord by position across ALL items (real + placeholder),
        # so the real lines land at ord 1 and ord 3 (ord 2 is the placeholder).
        rows = [self._row(1, 101, "A"), self._row(3, 307, "C")]
        items = [
            {"request_id": "r1", "request_order": 1, "source_row": 1,
             "requested_name": "", "requested_code": "", "requested_cas": "",
             "lines": [{"product_id": 101, "selection_order": 1}]},
            {"request_id": "r2", "request_order": 2, "source_row": 2,
             "requested_name": "Chem X", "requested_code": "CX", "requested_cas": "CAS-X",
             "lines": [],
             "placeholder": {"classification": "UNRESOLVED", "reason_code": "NO_MATCH"}},
            {"request_id": "r3", "request_order": 3, "source_row": 3,
             "requested_name": "", "requested_code": "", "requested_cas": "",
             "lines": [{"product_id": 307, "selection_order": 1}]},
        ]
        response, _conn, export_mock = self._post_items(rows, items)
        self.assertEqual(response.status_code, 200)
        products = export_mock.call_args.args[1]
        self.assertEqual([p["STT"] for p in products], ["1", "2", "3"])
        self.assertEqual([p["request_id"] for p in products], ["r1", "r2", "r3"])
        self.assertIsNone(products[1]["Unit_Price_Value"])
        self.assertEqual(products[1]["Name"], "Chem X")
        self.assertEqual(products[1]["Code"], "CX")
        self.assertEqual(products[1]["Cas"], "CAS-X")
        self.assertEqual(products[1]["Brand"], "")
        self.assertEqual(products[1]["Compliance_Combined"], "Không tìm thấy")

    def test_export_items_multi_select_then_placeholder_then_selected_stt(self):
        """Multi-select -> placeholder -> selected must keep STT 1, 1.1, 2, 3."""
        # r1 takes ord 1-2 (two lines), the placeholder takes ord 3, r3 takes ord 4.
        rows = [self._row(1, 101, "A"), self._row(2, 205, "B"), self._row(4, 307, "C")]
        items = [
            {"request_id": "r1", "request_order": 1, "source_row": 1,
             "requested_name": "", "requested_code": "", "requested_cas": "",
             "lines": [
                 {"product_id": 101, "selection_order": 1},
                 {"product_id": 205, "selection_order": 2},
             ]},
            {"request_id": "r2", "request_order": 2, "source_row": 2,
             "requested_name": "req2", "requested_code": "", "requested_cas": "",
             "lines": [],
             "placeholder": {"classification": "REVIEW", "reason_code": "MANUAL_SELECTION_REQUIRED"}},
            {"request_id": "r3", "request_order": 3, "source_row": 3,
             "requested_name": "", "requested_code": "", "requested_cas": "",
             "lines": [{"product_id": 307, "selection_order": 1}]},
        ]
        response, _conn, export_mock = self._post_items(rows, items)
        self.assertEqual(response.status_code, 200)
        products = export_mock.call_args.args[1]
        self.assertEqual([p["STT"] for p in products], ["1", "1.1", "2", "3"])
        self.assertEqual(products[2]["Compliance_Combined"], "Cần kiểm tra/chọn thủ công")

    def test_export_items_duplicate_requested_content_keeps_distinct_placeholders(self):
        """Two placeholders with identical Name/Code/CAS but distinct
        request_id must both be exported at their own position, not merged."""
        items = [
            {"request_id": "r1", "request_order": 1, "source_row": 1,
             "requested_name": "Same Chem", "requested_code": "SC", "requested_cas": "SC-CAS",
             "lines": [], "placeholder": {"classification": "UNRESOLVED", "reason_code": "NO_MATCH"}},
            {"request_id": "r2", "request_order": 2, "source_row": 2,
             "requested_name": "Same Chem", "requested_code": "SC", "requested_cas": "SC-CAS",
             "lines": [], "placeholder": {"classification": "UNRESOLVED", "reason_code": "NO_MATCH"}},
        ]
        response, _conn, export_mock = self._post_items([], items)
        self.assertEqual(response.status_code, 200)
        products = export_mock.call_args.args[1]
        self.assertEqual([p["STT"] for p in products], ["1", "2"])
        self.assertEqual([p["request_id"] for p in products], ["r1", "r2"])
        self.assertEqual([p["Name"] for p in products], ["Same Chem", "Same Chem"])

    def test_export_items_noncontiguous_source_row_does_not_affect_request_order(self):
        rows = [self._row(1, 101, "A")]
        items = [
            {"request_id": "r1", "request_order": 1, "source_row": 40,
             "requested_name": "", "requested_code": "", "requested_cas": "",
             "lines": [{"product_id": 101, "selection_order": 1}]},
            {"request_id": "r2", "request_order": 2, "source_row": 5,
             "requested_name": "req2", "requested_code": "", "requested_cas": "",
             "lines": [], "placeholder": {"classification": "UNRESOLVED", "reason_code": "NO_MATCH"}},
        ]
        response, _conn, export_mock = self._post_items(rows, items)
        self.assertEqual(response.status_code, 200)
        products = export_mock.call_args.args[1]
        self.assertEqual([p["STT"] for p in products], ["1", "2"])
        self.assertEqual([p["source_row"] for p in products], [40, 5])

    def test_export_items_blocked_placeholder_note_text(self):
        items = [
            {"request_id": "r1", "request_order": 1, "source_row": None,
             "requested_name": "req1", "requested_code": "", "requested_cas": "",
             "lines": [], "placeholder": {"classification": "BLOCKED", "reason_code": "COMPLIANCE_BLOCKED"}},
        ]
        response, _conn, export_mock = self._post_items([], items)
        self.assertEqual(response.status_code, 200)
        products = export_mock.call_args.args[1]
        self.assertEqual(
            products[0]["Compliance_Combined"],
            "Không thể báo giá: tất cả sản phẩm bị chặn compliance",
        )

    def test_export_items_blocked_placeholder_without_reason_uses_default_text(self):
        items = [
            {"request_id": "r1", "request_order": 1, "source_row": None,
             "requested_name": "req1", "requested_code": "", "requested_cas": "",
             "lines": [], "placeholder": {"classification": "BLOCKED"}},
        ]
        response, _conn, export_mock = self._post_items([], items)
        self.assertEqual(response.status_code, 200)
        products = export_mock.call_args.args[1]
        self.assertEqual(
            products[0]["Compliance_Combined"],
            "Không thể báo giá: không đủ điều kiện báo giá",
        )

    def test_export_items_all_placeholders_zero_selected_succeeds(self):
        """Export must succeed with matched results but zero selections."""
        items = [
            {"request_id": "r1", "request_order": 1, "source_row": None,
             "requested_name": "a", "requested_code": "", "requested_cas": "",
             "lines": [], "placeholder": {"classification": "UNRESOLVED", "reason_code": "NO_MATCH"}},
            {"request_id": "r2", "request_order": 2, "source_row": None,
             "requested_name": "b", "requested_code": "", "requested_cas": "",
             "lines": [], "placeholder": {"classification": "REVIEW", "reason_code": "MANUAL_SELECTION_REQUIRED"}},
        ]
        response, conn, export_mock = self._post_items([], items)
        self.assertEqual(response.status_code, 200)
        products = export_mock.call_args.args[1]
        self.assertEqual(len(products), 2)
        self.assertEqual([p["STT"] for p in products], ["1", "2"])
        # no product bulk query needed since there are no real lines at all
        product_queries = [q for q in conn.queries if "WITH input AS" in q]
        self.assertEqual(len(product_queries), 0)

    def test_export_items_rejects_invalid_or_missing_placeholder(self):
        missing_placeholder = [{"request_id": "r1", "request_order": 1, "lines": []}]
        response, _conn, _mock = self._post_items([], missing_placeholder)
        self.assertEqual(response.status_code, 400)

        bad_classification = [
            {"request_id": "r1", "request_order": 1, "lines": [],
             "placeholder": {"classification": "DONE"}},
        ]
        response, _conn, _mock = self._post_items([], bad_classification)
        self.assertEqual(response.status_code, 400)

        bad_reason_code = [
            {"request_id": "r1", "request_order": 1, "lines": [],
             "placeholder": {"classification": "UNRESOLVED", "reason_code": "NOT_A_REAL_CODE"}},
        ]
        response, _conn, _mock = self._post_items([], bad_reason_code)
        self.assertEqual(response.status_code, 400)

        mismatched_reason_vs_classification = [
            {"request_id": "r1", "request_order": 1, "lines": [],
             "placeholder": {"classification": "UNRESOLVED", "reason_code": "COMPLIANCE_BLOCKED"}},
        ]
        response, _conn, _mock = self._post_items([], mismatched_reason_vs_classification)
        self.assertEqual(response.status_code, 400)

    def test_legacy_selections_still_works_without_export_items(self):
        rows = [self._row(1, 42, "First")]
        fake_conn = FakeConnection(rows)
        search.app.testing = True
        with patch("search.get_connection", return_value=fake_conn), patch(
            "search.export_quick_quote_workbook", return_value=b"exported-xlsx"
        ) as export_mock:
            with search.app.test_client() as client:
                with client.session_transaction() as sess:
                    sess["authenticated"] = True
                    sess["user_id"] = 1
                    sess["auth_version"] = 1
                    sess["is_admin"] = True
                    sess["team_id"] = 1
                response = client.post(
                    "/api/quote-assistant/workbook/export",
                    data={
                        "workbook": (io.BytesIO(make_workbook()), "quote.xlsx"),
                        "selections": json.dumps([{"product_id": 42}]),
                    },
                    content_type="multipart/form-data",
                )
        self.assertEqual(response.status_code, 200)
        products = export_mock.call_args.args[1]
        self.assertEqual([p["product_id"] for p in products], [42])
        # legacy path has no STT label
        self.assertNotIn("STT", products[0])

    def test_payload_with_both_export_items_and_selections_prioritizes_export_items(self):
        rows = [self._row(1, 101, "A"), self._row(2, 205, "B")]
        fake_conn = FakeConnection(rows)
        search.app.testing = True
        items = [
            {"request_id": "r1", "request_order": 1, "source_row": None,
             "requested_name": "req1", "requested_code": "", "requested_cas": "",
             "lines": [{"product_id": 101, "selection_order": 1}]},
        ]
        legacy_selections = [{"product_id": 205}]
        with patch("search.get_connection", return_value=fake_conn), patch(
            "search.export_quick_quote_workbook", return_value=b"exported-xlsx"
        ) as export_mock:
            with search.app.test_client() as client:
                with client.session_transaction() as sess:
                    sess["authenticated"] = True
                    sess["user_id"] = 1
                    sess["auth_version"] = 1
                    sess["is_admin"] = True
                    sess["team_id"] = 1
                response = client.post(
                    "/api/quote-assistant/workbook/export",
                    data={
                        "workbook": (io.BytesIO(make_workbook()), "quote.xlsx"),
                        "export_items": json.dumps(items),
                        "selections": json.dumps(legacy_selections),
                    },
                    content_type="multipart/form-data",
                )
        self.assertEqual(response.status_code, 200)
        products = export_mock.call_args.args[1]
        # Must prioritize export_items (product 101), NOT duplicate or include selections (product 205)
        self.assertEqual([p["product_id"] for p in products], [101])
        self.assertEqual(products[0]["STT"], "1")
        self.assertEqual(products[0]["request_id"], "r1")


class QuoteWorkbookSttRenderingTests(unittest.TestCase):
    """Exporter renders STT column A with sub-ordinals and preserves protections."""

    def _products_with_stt(self, stts):
        out = []
        for stt in stts:
            p = product(stt if isinstance(stt, int) else 1)
            p["STT"] = stt
            out.append(p)
        return out

    def test_single_line_stt_written_as_number(self):
        products = self._products_with_stt([5])
        out = qwe.export_quick_quote_workbook(make_workbook(), products)
        root = _sheet_xml(out)
        self.assertEqual(_text(_cell(root, "A17")), "5")
        self.assertIsNone(_cell(root, "A17").attrib.get("t"))

    def test_three_lines_stt_written_as_5_5_1_5_2(self):
        products = self._products_with_stt([5, "5.1", "5.2"])
        out = qwe.export_quick_quote_workbook(make_workbook(), products)
        root = _sheet_xml(out)
        self.assertEqual(_text(_cell(root, "A17")), "5")
        self.assertIsNone(_cell(root, "A17").attrib.get("t"))
        self.assertEqual(_text(_cell(root, "A18")), "5.1")
        self.assertEqual(_cell(root, "A18").attrib.get("t"), "inlineStr")
        self.assertEqual(_text(_cell(root, "A19")), "5.2")
        self.assertEqual(_cell(root, "A19").attrib.get("t"), "inlineStr")

    def test_two_requests_stt_written_as_1_1_1_2(self):
        products = self._products_with_stt([1, "1.1", 2])
        out = qwe.export_quick_quote_workbook(make_workbook(), products)
        root = _sheet_xml(out)
        self.assertEqual(_text(_cell(root, "A17")), "1")
        self.assertIsNone(_cell(root, "A17").attrib.get("t"))
        self.assertEqual(_text(_cell(root, "A18")), "1.1")
        self.assertEqual(_cell(root, "A18").attrib.get("t"), "inlineStr")
        self.assertEqual(_text(_cell(root, "A19")), "2")
        self.assertIsNone(_cell(root, "A19").attrib.get("t"))

    def test_multi_line_workbook_preserves_footer_formulas_and_namespace(self):
        products = self._products_with_stt([1, "2.1", "2.2", 3])
        out = qwe.export_quick_quote_workbook(make_workbook(product_rows=9), products)
        root = _sheet_xml(out)
        # footer total row moved down by inserted rows when exceeding capacity
        # here capacity 9 >= 4, total row stays at 26
        self.assertIn("SUM(J17:J20)", _formula(_cell(root, "J26")))
        self.assertEqual(_formula(_cell(root, "J27")), "J26*0.08")
        sheet_data = _zip_entries(out)["xl/worksheets/custom_bg.xml"]
        prefixes = _declared_prefixes(sheet_data)
        for token in ["x14ac", "xr", "xr2", "xr3"]:
            self.assertIn(token, prefixes)
        # calcChain removed
        self.assertNotIn("xl/calcChain.xml", _zip_entries(out))
        # calcPr forces recalc
        workbook = _zip_entries(out)["xl/workbook.xml"].decode()
        self.assertIn("fullCalcOnLoad", workbook)

    def test_placeholder_mixed_with_products_at_1_9_12_line_boundaries(self):
        """Phase 4A: placeholders interleaved with real product rows must not
        break footer formulas, calcChain removal, or full-recalc protection at
        the 1/exact-capacity(9)/over-capacity(12) boundaries."""
        def placeholder_line(idx):
            return {
                "Name": f"req{idx}", "Code": "", "Cas": "", "Brand": "", "Size": "",
                "Note": "", "Compliance_Combined": "Không tìm thấy", "Unit_Price_Value": None,
            }

        out1 = qwe.export_quick_quote_workbook(make_workbook(), [placeholder_line(1)])
        root1 = _sheet_xml(out1)
        self.assertEqual(_text(_cell(root1, "P17")), "")
        self.assertEqual(_formula(_cell(root1, "J26")), "SUM(J17:J17)")

        items9 = [placeholder_line(i) if i in (1, 5, 9) else product(i) for i in range(1, 10)]
        out9 = qwe.export_quick_quote_workbook(make_workbook(), items9)
        root9 = _sheet_xml(out9)
        self.assertEqual(_formula(_cell(root9, "J26")), "SUM(J17:J25)")
        for row in (17, 21, 25):
            self.assertEqual(_text(_cell(root9, f"P{row}")), "")
        self.assertNotIn("xl/calcChain.xml", _zip_entries(out9))

        items12 = [placeholder_line(i) if i in (1, 6, 12) else product(i) for i in range(1, 13)]
        out12 = qwe.export_quick_quote_workbook(make_workbook(), items12)
        root12 = _sheet_xml(out12)
        self.assertEqual(_formula(_cell(root12, "J29")), "SUM(J17:J28)")
        self.assertEqual(_text(_cell(root12, "P17")), "")
        self.assertNotIn("xl/calcChain.xml", _zip_entries(out12))
        workbook12 = _zip_entries(out12)["xl/workbook.xml"].decode()
        self.assertIn("fullCalcOnLoad", workbook12)


if __name__ == "__main__":
    unittest.main()

