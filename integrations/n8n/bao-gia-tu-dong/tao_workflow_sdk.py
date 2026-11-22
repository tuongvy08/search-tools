# -*- coding: utf-8 -*-
"""Sinh mã SDK workflow n8n (workflow_sdk.ts) từ 3 file JS đã kiểm chứng.

    .venv/bin/python integrations/n8n/bao-gia-tu-dong/tao_workflow_sdk.py <thư mục ra>

Mã sinh ra được dán vào công cụ n8n (validate/create workflow). Thẻ KHÔNG nằm trong mã: workflow dùng credential
Header Auth tên Search_Tools_Machine_API_Noi_bo do người quản trị tạo trong n8n.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else HERE

COLS = ["STT", "Trang_thai", "Ly_do_can_xu_ly", "Yeu_cau_goc", "Ma_hang", "Ten_san_pham", "Hang", "Quy_cach", "So_luong",
        "Gia_goc", "He_so_ship", "Tien_te", "Ty_gia_VND", "Don_gia_VND", "Thanh_tien_VND", "Ton_kho", "So_luong_ton",
        "Du_hang", "Han_dung_gan_nhat", "Phap_che_nhan", "Chan_bao_gia", "Ghi_chu_phap_che", "So_ung_vien",
        "Ung_vien_goi_y", "Ghi_chu_he_thong", "Chay_luc"]
SHEET_ID = "12h7uPdmEFOiIS_cRCkBXF4wtaQsU25cn1lQqCz78ghw"
BASE = "https://staging.search.standards.vn/machine/v1"


def js(name):
    return open(os.path.join(HERE, name), encoding="utf-8").read()


def schema(cols, match):
    return ",\n        ".join(
        "{ id: '%s', displayName: '%s', required: false, defaultMatch: %s, display: true, type: 'string', canBeUsedToMatch: true }"
        % (c, c, "true" if c == match else "false") for c in cols)


DOC = ("{ __rl: true, mode: 'id', value: '%s', cachedResultName: 'Search-tools — Thử báo giá tự động qua API (staging)' }"
       % SHEET_ID)
CRED = "{ googleSheetsOAuth2Api: { id: '9yk610jPHsEikVHe', name: 'Google Sheets account' } }"


def sheet_write(var, title, pos, tab, cols, match, sample):
    return f"""const {var} = node({{
  type: 'n8n-nodes-base.googleSheets',
  version: 4.7,
  config: {{
    name: '{title}',
    position: [{pos}, 300],
    credentials: {CRED},
    parameters: {{
      resource: 'sheet',
      operation: 'appendOrUpdate',
      documentId: {DOC},
      sheetName: {{ __rl: true, mode: 'name', value: '{tab}' }},
      columns: {{
        mappingMode: 'autoMapInputData',
        value: {{}},
        matchingColumns: ['{match}'],
        schema: [
        {schema(cols, match)}
        ],
      }},
      options: {{ cellFormat: 'RAW', handlingExtraData: 'ignoreIt' }},
    }},
  }},
  output: [{sample}],
}});
"""


code = f"""import {{ workflow, node, trigger, sticky, newCredential, expr }} from '@n8n/workflow-sdk';

const startTrigger = trigger({{
  type: 'n8n-nodes-base.manualTrigger',
  version: 1,
  config: {{ name: 'Bat dau (chay tay)', position: [0, 300] }},
  output: [{{}}],
}});

const readRequests = node({{
  type: 'n8n-nodes-base.googleSheets',
  version: 4.7,
  config: {{
    name: 'Doc yeu cau tu Google Sheet',
    position: [240, 300],
    credentials: {CRED},
    parameters: {{
      resource: 'sheet',
      operation: 'read',
      documentId: {DOC},
      sheetName: {{ __rl: true, mode: 'name', value: 'Yeu_cau' }},
      options: {{ outputFormatting: {{ values: {{ general: 'UNFORMATTED_VALUE', date: 'FORMATTED_STRING' }} }} }},
    }},
  }},
  output: [{{ STT: 1, Ma_hang: 'T0050000', CAS: '', Ten_san_pham: '', Hang: '', Quy_cach: '', So_luong: 2 }}],
}});

const normalizeRequests = node({{
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {{
    name: 'Chuan hoa yeu cau',
    position: [480, 300],
    parameters: {{
      mode: 'runOnceForAllItems',
      language: 'javaScript',
      jsCode: `{js('chuan_hoa_yeu_cau.js')}`,
    }},
  }},
  output: [{{ STT: '1', mode: 'code', q: 'T0050000', code: 'T0050000', cas: '', name: '', brand: '', size: '', qty: 2, qtyError: '', skipReason: '', dupOf: '', can_lookup: true }}],
}});

const lookupProducts = node({{
  type: 'n8n-nodes-base.httpRequest',
  version: 4.5,
  config: {{
    name: 'Tra san pham',
    position: [720, 300],
    onError: 'continueRegularOutput',
    credentials: {{ httpTemplatedCustomAuth: newCredential('Search_Tools_Machine_API_Noi_bo') }},
    parameters: {{
      method: 'GET',
      url: expr('{{{{ $json.can_lookup ? "{BASE}/products" : "{BASE}/health" }}}}'),
      authentication: 'genericCredentialType',
      genericAuthType: 'httpTemplatedCustomAuth',
      sendQuery: true,
      specifyQuery: 'keypair',
      queryParameters: {{ parameters: [
        {{ name: 'q', value: expr('{{{{ $json.can_lookup ? $json.q : "" }}}}') }},
        {{ name: 'limit', value: '50' }},
      ] }},
      options: {{
        batching: {{ batch: {{ batchSize: 1, batchInterval: 400 }} }},
        response: {{ response: {{ fullResponse: true, neverError: true, responseFormat: 'json' }} }},
        timeout: 20000,
      }},
    }},
  }},
  output: [{{ statusCode: 200, body: {{ count: 1, more: false, results: [] }} }}],
}});

const buildQuote = node({{
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {{
    name: 'Lap bao gia nhap',
    position: [960, 300],
    parameters: {{
      mode: 'runOnceForAllItems',
      language: 'javaScript',
      jsCode: `{js('lap_bao_gia_nhap.js')}`,
    }},
  }},
  output: [{{ STT: '1', Trang_thai: 'ĐÃ KHỚP', Ma_hang: 'T0050000', Don_gia_VND: 2977000 }}],
}});

{sheet_write('writeQuote', 'Ghi tab Bao_gia_nhap', 1200, 'Bao_gia_nhap', COLS, 'STT', "{ STT: '1', Trang_thai: 'ĐÃ KHỚP' }")}
const buildSummary = node({{
  type: 'n8n-nodes-base.code',
  version: 2,
  config: {{
    name: 'Tong ket',
    position: [1440, 300],
    executeOnce: true,
    parameters: {{
      mode: 'runOnceForAllItems',
      language: 'javaScript',
      jsCode: `{js('tong_ket.js')}`,
    }},
  }},
  output: [{{ Chi_so: 'DA_KHOP', Gia_tri: 9, Chay_luc: '2026-11-21T00:00:00.000Z' }}],
}});

{sheet_write('writeSummary', 'Ghi tab Tong_ket', 1680, 'Tong_ket', ['Chi_so', 'Gia_tri', 'Chay_luc'], 'Chi_so', "{ Chi_so: 'DA_KHOP', Gia_tri: 9 }")}
const guide = sticky(
  '## Bao gia nhap tu dong (STAGING)\\nDoc tab Yeu_cau, tra cuu cong API chi doc (the NOI BO co gia), ghi tab Bao_gia_nhap va Tong_ket.\\nChi tu dien khi xac dinh duoc DUNG MOT san pham; con lai ghi CAN NGUOI XU LY.\\nCredential can tao: kieu Custom Auth (templated), ten Search_Tools_Machine_API_Noi_bo, header Authorization = Bearer + the noi bo (xem README). Khong dung the cua chatbot khach.',
  [startTrigger, lookupProducts],
  {{ color: 4 }},
);

export default workflow('bao-gia-nhap-staging', 'Search-tools - Bao gia nhap tu dong (staging)')
  .add(guide)
  .add(startTrigger)
  .to(readRequests)
  .to(normalizeRequests)
  .to(lookupProducts)
  .to(buildQuote)
  .to(writeQuote)
  .to(buildSummary)
  .to(writeSummary);
"""

with open(os.path.join(OUT, "workflow_sdk.ts"), "w", encoding="utf-8") as handle:
    handle.write(code)
print(len(code))
