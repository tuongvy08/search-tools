(function () {
  'use strict';
  function payload(form, before) {
    var action = form.elements.action.value;
    var result = {action: action, request_id: form.elements.request_id.value, reason: form.elements.reason.value};
    if (before.id) { result.rule_id = before.id; result.expected_revision = before.revision; }
    if (action === 'create' || action === 'edit') {
      ['match_field', 'match_value', 'status_id', 'note'].forEach(function (key) { result[key] = form.elements[key].value; });
    }
    return result;
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {payload: payload};
  if (typeof document === 'undefined') return;
  document.addEventListener('DOMContentLoaded', function () {
    var form = document.getElementById('manualRuleForm');
    if (!form) return;
    var before = JSON.parse(document.getElementById('regulatoryRuleData').textContent);
    var fieldLabels = JSON.parse(document.getElementById('regulatoryFieldLabels').textContent);
    var review = document.getElementById('manualReview'), comparison = document.getElementById('manualComparison');
    var warnings = document.getElementById('manualWarnings'), error = document.getElementById('manualError');
    var confirm = document.getElementById('manualConfirm'), preview = document.getElementById('manualPreview');
    var reviewed = null, generation = 0, saving = false;
    function uuid() {
      var bytes = new Uint8Array(16); window.crypto.getRandomValues(bytes);
      bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
      var hex = Array.from(bytes, function (n) { return n.toString(16).padStart(2, '0'); }).join('');
      return hex.slice(0,8)+'-'+hex.slice(8,12)+'-'+hex.slice(12,16)+'-'+hex.slice(16,20)+'-'+hex.slice(20);
    }
    function showError(data) {
      error.textContent = data.error || 'Không thể lưu. Hãy kiểm tra rồi thử lại.';
      if (data.rule_url) {
        var link = document.createElement('a'); link.href = data.rule_url;
        link.textContent = ' Mở quy tắc có sẵn'; error.appendChild(link);
      }
    }
    async function post(url, data) {
      var response = await fetch(url, {method:'POST', credentials:'same-origin',
        headers:{'Content-Type':'application/json','X-CSRF-Token':form.elements.csrf_token.value}, body:JSON.stringify(data)});
      var result = await response.json().catch(function () { return {error:'Phiên không hợp lệ hoặc yêu cầu bị từ chối. Hãy tải lại trang.'}; });
      if (!response.ok) { showError(result); return null; }
      return result;
    }
    function invalidate() {
      generation++; reviewed = null; review.hidden = true;
      form.elements.request_id.value = uuid();
    }
    form.addEventListener('input', invalidate);
    form.addEventListener('change', invalidate);
    form.addEventListener('submit', async function (event) {
      event.preventDefault(); if (saving || !form.reportValidity()) return;
      error.textContent = ''; review.hidden = true; reviewed = null;
      var data = payload(form, before), version = ++generation;
      if ((data.action === 'deactivate' || (before.id && data.action === 'edit' && Number(data.status_id) !== before.status_id)) && !data.reason.trim()) {
        showError({error:'Đổi tình trạng hoặc ngừng áp dụng phải nhập lý do.'}); return;
      }
      preview.disabled = true;
      try {
        var related = [];
        if (data.action !== 'deactivate') {
          var check = data.action === 'restore' ? before : data;
          var result = await post(form.dataset.checkUrl, {match_field:check.match_field,match_value:check.match_value,
            status_id:check.status_id,rule_id:before.id || null});
          if (!result || generation !== version) return;
          related = result.related;
        }
        if (generation !== version) return;
        comparison.textContent = '';
        var next = Object.assign({}, before, data);
        next.is_active = data.action === 'deactivate' ? false : data.action === 'restore' || data.action === 'create' ? true : before.is_active;
        ['match_field','match_value','status_id','note','is_active','reason'].forEach(function (key) {
          var labels = {match_field:'Loại',match_value:'Giá trị',status_id:'Tình trạng',note:'Ghi chú',is_active:'Áp dụng',reason:'Lý do'};
          function display(value) {
            if (key === 'match_field') return fieldLabels[value] || '—';
            if (key === 'status_id') {
              var option = Array.from(form.elements.status_id.options).find(function (o) { return String(o.value) === String(value); });
              return option ? option.textContent : '—';
            }
            if (key === 'is_active' && value !== undefined) return value ? 'Đang áp dụng' : 'Ngừng áp dụng';
            return value == null ? '—' : String(value);
          }
          var line = document.createElement('p'); line.textContent = labels[key]+': '+display(before[key])+' → '+display(next[key]);
          comparison.appendChild(line);
        });
        warnings.textContent = related.length ? 'Cùng CAS/mã còn có tình trạng khác; kết quả phụ thuộc ưu tiên: '+
          related.map(function (r) { return '#'+r.id+' '+r.rule_label+' ('+(r.is_active?'đang áp dụng':'ngừng áp dụng')+')'; }).join('; ') : '';
        reviewed = data; review.hidden = false; confirm.disabled = false;
      } catch (_) { showError({error:'Không kiểm tra được dữ liệu. Hãy thử lại.'}); }
      finally { preview.disabled = false; }
    });
    confirm.addEventListener('click', async function () {
      if (!reviewed || saving) return;
      saving = true; confirm.disabled = true; preview.disabled = true;
      var controls = Array.from(form.querySelectorAll('input,select,textarea'));
      controls.forEach(function (node) { node.disabled = true; });
      try {
        var result = await post(form.dataset.saveUrl, reviewed);
        if (result) window.location.assign(result.url);
      } catch (_) { showError({error:'Chưa nhận được kết quả. Có thể bấm xác nhận lại an toàn.'}); }
      finally {
        saving = false; confirm.disabled = false; preview.disabled = false;
        controls.forEach(function (node) { node.disabled = false; });
      }
    });
  });
}());
