'use strict';
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const {webcrypto} = require('crypto');
const {payload} = require('../static/regulatory_manual.js');

class Node {
  constructor(){this.listeners={};this.children=[];this.textContent='';this.hidden=false;this.disabled=false;this.value='';}
  addEventListener(event,fn){this.listeners[event]=fn;}
  appendChild(node){this.children.push(node);}
  async fire(event){if(this.listeners[event])return this.listeners[event]({preventDefault(){}});}
}
async function test(field){
  const labels={cas:'CAS',code:'Mã sản phẩm',name:'Tên sản phẩm'};
  const ids={}; ['manualRuleForm','manualReview','manualComparison','manualWarnings','manualError','manualConfirm','manualPreview','regulatoryRuleData','regulatoryFieldLabels'].forEach(id=>ids[id]=new Node());
  const form=ids.manualRuleForm;
  form.elements={};
  for(const [key,value] of Object.entries({action:'create',request_id:'original',reason:'',match_field:field,match_value:'<script>literal</script>',status_id:'1',note:'test',csrf_token:'csrf'})){
    form.elements[key]=new Node();form.elements[key].value=value;
  }
  form.elements.status_id.options=[{value:'1',textContent:'Cấm nhập'}];
  form.dataset={checkUrl:'/check',saveUrl:'/save'};
  form.reportValidity=()=>true;
  form.querySelectorAll=()=>Object.values(form.elements);
  ids.regulatoryRuleData.textContent='{}';
  ids.regulatoryFieldLabels.textContent=JSON.stringify(labels);
  let ready; const requests=[]; let loseResponse=true;
  const context={document:{addEventListener:(e,fn)=>{ready=fn;},getElementById:id=>ids[id],createElement:()=>new Node()},
    window:{crypto:webcrypto,location:{assign:url=>{context.redirect=url;}}},Uint8Array,JSON,Array,Object,String,Number,
    fetch:async(url,options)=>{
      requests.push({url,data:JSON.parse(options.body)});
      if(url==='/save' && loseResponse){loseResponse=false;throw new Error('lost response');}
      return {ok:true,json:async()=>url==='/check'?{related:[{id:4,rule_label:'Khác',is_active:true}]}:{url:'/done'}};
    }};
  vm.runInNewContext(fs.readFileSync(require.resolve('../static/regulatory_manual.js'),'utf8'),context);
  ready();
  await form.fire('submit');
  assert.strictEqual(ids.manualReview.hidden,false);
  assert.ok(ids.manualWarnings.textContent.includes('Khác'));
  assert.strictEqual(requests.filter(r=>r.url==='/save').length,0);
  assert.ok(ids.manualComparison.children.some(n=>n.textContent.includes('<script>literal</script>'))); // text, not innerHTML
  assert.ok(ids.manualComparison.children.some(n=>n.textContent==='Loại: — → '+labels[field]));
  assert.strictEqual(requests[0].data.match_field,field);
  await ids.manualConfirm.fire('click');
  await ids.manualConfirm.fire('click');
  const saves=requests.filter(r=>r.url==='/save');
  assert.deepStrictEqual(saves[0].data,saves[1].data);
  assert.strictEqual(saves[0].data.match_field,field);
  assert.strictEqual(context.redirect,'/done');
  form.elements.note.value='modified'; await form.fire('input');
  assert.strictEqual(ids.manualReview.hidden,true);
  const count=requests.length; await ids.manualConfirm.fire('click'); assert.strictEqual(requests.length,count);
  const before={id:9,revision:5};form.elements.action.value='deactivate';
  assert.deepStrictEqual(Object.keys(payload(form,before)).sort(),['action','expected_revision','reason','request_id','rule_id']);
  // Edit preview must label both sides, while expected revision and machine field stay intact.
  const beforeEdit={id:9,revision:5,match_field:field,match_value:'OLD',status_id:1,note:'old',is_active:true};
  ids.regulatoryRuleData.textContent=JSON.stringify(beforeEdit);
  form.elements.action.value='edit';form.elements.match_value.value='NEW';
  ids.manualComparison.children=[];ready();await form.fire('submit');
  assert.ok(ids.manualComparison.children.some(n=>n.textContent==='Loại: '+labels[field]+' → '+labels[field]));
  assert.strictEqual(payload(form,beforeEdit).match_field,field);
  assert.strictEqual(payload(form,beforeEdit).expected_revision,5);
  console.log('regulatory manual DOM: '+field+' labels, unchanged payload/revision, preview-only, retry, invalidation PASS');
}
(async()=>{for(const field of ['cas','code','name'])await test(field);})().catch(error=>{console.error(error);process.exit(1);});
