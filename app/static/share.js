(()=>{
  const esc=value=>String(value??'').replace(/[&<>'"]/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
  const id=decodeURIComponent(location.pathname.replace(/^\/live\//,''));
  const card=document.getElementById('share-card');
  const row=(label,value)=>value?`<div><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`:'';
  fetch(`/api/share/${encodeURIComponent(id)}`,{cache:'no-store'}).then(async response=>{
    const payload=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(payload.detail||'This live activity is no longer available.');
    document.title=`${payload.name} — UK Activity Radar`;
    const facts=[row('Status',payload.status),row('Why it matters',payload.reason),row('Route',payload.route),row('From',payload.origin),row('To',payload.destination),row('Broad area',payload.location),row('Position confidence',payload.position_confidence),row('Source',payload.source)].join('');
    const updated=payload.freshness?new Date(payload.freshness).toLocaleString('en-GB',{dateStyle:'medium',timeStyle:'short'}):'Live feed';
    card.innerHTML=`<div class="eyebrow">${esc(payload.badge||payload.type||'LIVE ACTIVITY')}</div><h1>${esc(payload.name)}</h1><p class="subtitle">${esc(payload.subtitle||payload.status||'Current live activity')}</p><div class="share-facts">${facts}</div><div class="truth"><strong>${payload.sensitive?'Safety-protected live context':'Safe public live page'}</strong><span>${esc(payload.safety_note)}</span><small>Updated ${esc(updated)}</small></div><a class="open-radar" href="/">Open UK Activity Radar</a>`;
  }).catch(error=>{card.innerHTML=`<div class="eyebrow">LIVE FEED</div><h1>Activity unavailable</h1><p class="subtitle">${esc(error.message)}</p><a class="open-radar" href="/">Open UK Activity Radar</a>`;});
})();
