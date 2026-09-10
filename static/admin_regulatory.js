document.addEventListener('DOMContentLoaded',function(){
  var paletteClasses=['regulatory-color-gray','regulatory-color-red','regulatory-color-amber','regulatory-color-teal','regulatory-color-green','regulatory-color-blue','regulatory-color-purple','regulatory-color-custom'];
  var canonicalHex=/^#[0-9A-F]{6}$/;
  function colorPair(seed){
    var value=String(seed||'').toUpperCase();
    if(!canonicalHex.test(value))return null;
    var rgb=[1,3,5].map(function(index){return parseInt(value.slice(index,index+2),16);});
    var tint=rgb.map(function(channel){return Math.floor((channel*18+255*82+50)/100);});
    var bg='#'+tint.map(function(channel){return channel.toString(16).padStart(2,'0').toUpperCase();}).join('');
    function luminance(hex){
      var channels=[1,3,5].map(function(index){return parseInt(hex.slice(index,index+2),16)/255;});
      var linear=channels.map(function(item){return item<=0.04045?item/12.92:Math.pow((item+0.055)/1.055,2.4);});
      return 0.2126*linear[0]+0.7152*linear[1]+0.0722*linear[2];
    }
    function contrast(a,b){var values=[luminance(a),luminance(b)].sort(function(x,y){return y-x;});return(values[0]+0.05)/(values[1]+0.05);}
    var fg=contrast(bg,'#111827')>=contrast(bg,'#FFFFFF')?'#111827':'#FFFFFF';
    return {bg:bg,fg:fg};
  }
  document.querySelectorAll('[data-color-form]').forEach(function(form){
    var save=form.querySelector('[data-save-color]');
    var customChoice=form.querySelector('[data-custom-color-choice]');
    var customInput=form.querySelector('[data-custom-color-input]');
    var output=form.querySelector('[data-color-hex]');
    function preview(input,pair){
      var seed=String(input.value||'').toUpperCase();
      pair=pair||colorPair(seed);
      if(!pair)return;
      var row=form.closest('[data-status-color-preview]');
      var badge=row&&row.querySelector('.compliance-badge');
      [row,badge].forEach(function(node){
        if(!node)return;
        paletteClasses.forEach(function(css){node.classList.remove(css);});
        node.classList.add('regulatory-color-custom');
        node.style.setProperty('--reg-bg',pair.bg);
        node.style.setProperty('--reg-fg',pair.fg);
      });
      if(output)output.textContent=seed;
      if(save)save.disabled=false;
    }
    form.addEventListener('change',function(event){
      if(!event.target.matches('input[name="color_hex"]'))return;
      var pair=event.target.dataset.colorBg?{bg:event.target.dataset.colorBg,fg:event.target.dataset.colorFg}:null;
      preview(event.target,pair);
    });
    if(customInput&&customChoice){
      function chooseCustom(){
        customChoice.checked=true;
        customChoice.value=customInput.value.toUpperCase();
        preview(customChoice);
      }
      customInput.addEventListener('input',chooseCustom);
      customInput.addEventListener('change',chooseCustom);
    }
  });
  var card=document.querySelector('[data-job-status-url]');
  if(!card)return;
  var pill=card.querySelector('[data-job-status]');
  if(!pill||!['queued','running'].includes(pill.dataset.jobState||''))return;
  var stopped=false;
  async function poll(){
    if(stopped||document.hidden){window.setTimeout(poll,1500);return;}
    try{
      var response=await fetch(card.dataset.jobStatusUrl,{credentials:'same-origin',cache:'no-store'});
      if(!response.ok)throw new Error('status');
      var data=await response.json();
      pill.textContent=data.status_label||'Không xác định';pill.dataset.jobState=data.status;
      var phase=card.querySelector('[data-job-phase]');if(phase)phase.textContent=data.phase_label||'Không xác định';
      if(['completed','failed','cancelled'].includes(data.status)){stopped=true;window.location.reload();return;}
    }catch(_error){/* keep the current screen; next poll may recover */}
    window.setTimeout(poll,1500);
  }
  window.setTimeout(poll,800);
});
