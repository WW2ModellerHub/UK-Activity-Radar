(()=>{
  const esc=value=>String(value??'').replace(/[&<>'"]/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
  const id=decodeURIComponent(location.pathname.replace(/^\/live\//,''));
  const card=document.getElementById('share-card');
  const openUrl=`/?open=${encodeURIComponent(id)}`;
  const row=(label,value,wide=false)=>value?`<div${wide?' class="wide"':''}><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`:'';
  // Place names arrive in capitals from railway feeds.
  const place=value=>String(value||'').trim().split(/\s+/).map(word=>/[a-z]/.test(word)?word:word.charAt(0)+word.slice(1).toLowerCase()).join(' ');
  const ago=iso=>{
    const minutes=Math.round((Date.now()-new Date(iso).getTime())/60000);
    if(!Number.isFinite(minutes))return '';
    return minutes<1?'Just now':minutes<60?`${minutes} min ago`:new Date(iso).toLocaleString('en-GB',{dateStyle:'medium',timeStyle:'short'});
  };
  // Feed wording ("TD live • next berth unresolved") is not for people.
  const plainStatus=text=>/berth|TD live|TRUST|unresolved|describer/i.test(String(text||''))?'':text;
  const running=payload=>{
    if(payload.cancelled)return 'Cancelled';
    const delay=Number(payload.delay_minutes);
    if(payload.delay_minutes==null||!Number.isFinite(delay))return '';
    return delay>=1?`${Math.round(delay)} min late`:delay<=-1?`${-Math.round(delay)} min early`:'On time';
  };
  fetch(`/api/share/${encodeURIComponent(id)}`,{cache:'no-store'}).then(async response=>{
    const payload=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(payload.detail||'This live activity is no longer available.');
    document.title=`${payload.name} — UK Activity Radar`;
    const journey=payload.origin&&payload.destination?`${place(payload.origin)} → ${place(payload.destination)}`:payload.destination?`To ${place(payload.destination)}`:'';
    const where=payload.near||payload.location;
    const subtitle=journey||where||plainStatus(payload.subtitle)||'Current live activity';
    const height=payload.altitude_ft!=null?`${Math.round(Number(payload.altitude_ft)).toLocaleString('en-GB')} ft`:'';
    const facts=[
      row('Running',running(payload)),
      row('Where',journey?where:''),  // otherwise it is already the line under the name
      row('Next',payload.next_stop),
      row('Operator',payload.operator),
      row('Aircraft',payload.aircraft_model),
      row('Height',height),
      row('Speed',payload.speed),
      row('Status',plainStatus(payload.status)),
      row('What it is',payload.what_it_is||payload.reason,true),
      row('Its code',payload.code_note,true),
      row('Seen by',[payload.how_seen,payload.source].filter(Boolean).join(' · '),true),
    ].join('');
    const seen=payload.freshness?ago(payload.freshness):'';
    card.innerHTML=`<div class="eyebrow">${esc(payload.badge||payload.type||'LIVE ACTIVITY')}</div><h1>${esc(payload.name)}</h1><p class="subtitle">${esc(subtitle)}</p>${seen?`<p class="seen">Last seen <strong>${esc(seen)}</strong></p>`:''}<div class="share-facts">${facts}</div><div class="truth"><strong>${payload.sensitive?'Safety-protected live context':'Shared without exact location'}</strong><span>${esc(payload.safety_note)}</span></div><a class="open-radar" href="${esc(payload.open_url||openUrl)}">Show it on the live map</a>`;
  }).catch(error=>{card.innerHTML=`<div class="eyebrow">LIVE FEED</div><h1>Activity unavailable</h1><p class="subtitle">${esc(error.message)}</p><a class="open-radar" href="/">Open UK Activity Radar</a>`;});
})();
