document.addEventListener('DOMContentLoaded',function(){
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
