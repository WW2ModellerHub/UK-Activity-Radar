(function(root){
  'use strict';

  const clamp=value=>Math.max(0,Math.min(1,Number(value)||0));
  const radians=value=>Number(value)*Math.PI/180;
  const motionStates=new Map();
  let lastPrune=0;

  function distanceKm(lat1,lon1,lat2,lon2){
    const radius=6371;
    const dLat=radians(lat2-lat1),dLon=radians(lon2-lon1);
    const a=Math.sin(dLat/2)**2+Math.cos(radians(lat1))*Math.cos(radians(lat2))*Math.sin(dLon/2)**2;
    return radius*2*Math.atan2(Math.sqrt(a),Math.sqrt(1-a));
  }

  function usablePath(points){
    return (Array.isArray(points)?points:[])
      .map(point=>[Number(point?.[0]),Number(point?.[1])])
      .filter(point=>Number.isFinite(point[0])&&Number.isFinite(point[1]));
  }

  function pathLegs(points){
    const usable=usablePath(points),legs=[];let total=0;
    for(let index=1;index<usable.length;index+=1){
      const length=distanceKm(...usable[index-1],...usable[index]);
      if(!Number.isFinite(length)||length<=0)continue;
      legs.push({from:usable[index-1],to:usable[index],length,start:total});total+=length;
    }
    return {usable,legs,total};
  }

  function samplePath(points,fraction){
    const {usable,legs,total}=pathLegs(points);
    if(usable.length<2||!legs.length)return null;
    const target=clamp(fraction)*total;
    const leg=legs.find(item=>target<=item.start+item.length)||legs[legs.length-1];
    const local=clamp((target-leg.start)/leg.length);
    const lat=leg.from[0]+(leg.to[0]-leg.from[0])*local;
    const lon=leg.from[1]+(leg.to[1]-leg.from[1])*local;
    const lat1=radians(leg.from[0]),lat2=radians(leg.to[0]),deltaLon=radians(leg.to[1]-leg.from[1]);
    const y=Math.sin(deltaLon)*Math.cos(lat2);
    const x=Math.cos(lat1)*Math.sin(lat2)-Math.sin(lat1)*Math.cos(lat2)*Math.cos(deltaLon);
    const bearing=(Math.atan2(y,x)*180/Math.PI+360)%360;
    const progress=[usable[0]];
    for(const item of legs){if(item.start+item.length>=target)break;progress.push(item.to);}
    if(distanceKm(...progress[progress.length-1],lat,lon)>.001)progress.push([lat,lon]);
    return {lat,lon,bearing,total,progress,points:usable,fraction:clamp(fraction)};
  }

  // Find the closest fraction on mapped railway geometry. Corrections then
  // travel along that geometry instead of drawing a chord across the map.
  function nearestPathFraction(points,lat,lon){
    const {legs,total}=pathLegs(points);
    if(!legs.length||!Number.isFinite(Number(lat))||!Number.isFinite(Number(lon)))return null;
    const targetLat=Number(lat),targetLon=Number(lon),latScale=Math.cos(radians(targetLat));
    let best=null;
    for(const leg of legs){
      const ax=(leg.from[1]-targetLon)*latScale,ay=leg.from[0]-targetLat;
      const bx=(leg.to[1]-targetLon)*latScale,by=leg.to[0]-targetLat;
      const dx=bx-ax,dy=by-ay,denominator=dx*dx+dy*dy;
      const local=denominator>0?clamp(-(ax*dx+ay*dy)/denominator):0;
      const projectedLat=leg.from[0]+(leg.to[0]-leg.from[0])*local;
      const projectedLon=leg.from[1]+(leg.to[1]-leg.from[1])*local;
      const distance=distanceKm(targetLat,targetLon,projectedLat,projectedLon);
      if(!best||distance<best.distance){
        best={fraction:(leg.start+leg.length*local)/total,distance,lat:projectedLat,lon:projectedLon};
      }
    }
    return best;
  }

  function projectPosition(lat,lon,bearing,distance){
    const angular=distance/6371,heading=radians(bearing),p1=radians(lat),l1=radians(lon);
    const p2=Math.asin(Math.sin(p1)*Math.cos(angular)+Math.cos(p1)*Math.sin(angular)*Math.cos(heading));
    const l2=l1+Math.atan2(Math.sin(heading)*Math.sin(angular)*Math.cos(p1),Math.cos(angular)-Math.sin(p1)*Math.sin(p2));
    return {lat:p2*180/Math.PI,lon:((l2*180/Math.PI+540)%360)-180};
  }

  function ease(value){
    const progress=clamp(value);
    return progress<.5?4*progress*progress*progress:1-Math.pow(-2*progress+2,3)/2;
  }

  function correctionDuration(distance){
    return Math.max(280,Math.min(2600,280+Math.sqrt(Math.max(0,distance))*720));
  }

  function stateKey(activity){return `${activity?.type||'object'}:${activity?.id||activity?.source_id||activity?.name||''}`;}

  function remember(key,state,now){
    state.lastSeen=now;motionStates.set(key,state);
    if(now-lastPrune>60000){
      lastPrune=now;
      for(const [storedKey,stored] of motionStates){if(now-(stored.lastSeen||0)>300000)motionStates.delete(storedKey);}
    }
    return state;
  }

  function trainPresentation(activity,now,reducedMotion){
    const started=Date.parse(activity.estimate_started_at||''),due=Date.parse(activity.estimate_due_at||'');
    const path=usablePath(activity.estimate_path);
    if(reducedMotion||path.length<2||!Number.isFinite(started)||!Number.isFinite(due)||due<=started||now<started)return activity;
    const targetFraction=clamp((now-started)/(due-started));
    const target=samplePath(path,targetFraction);
    if(!target)return activity;
    const key=stateKey(activity);
    const signature=`${activity.estimate_started_at||''}|${activity.estimate_due_at||''}|${JSON.stringify(path)}`;
    let state=motionStates.get(key);
    if(!state||state.kind!=='train'){
      state=remember(key,{kind:'train',signature,displayed:target},now);
    }else if(state.signature!==signature){
      const projected=nearestPathFraction(path,state.displayed?.lat,state.displayed?.lon);
      const fromFraction=projected?.fraction??targetFraction;
      const from=samplePath(path,fromFraction)||target;
      state={kind:'train',signature,displayed:from,fromFraction,correctionStarted:now,
        correctionMs:correctionDuration(distanceKm(from.lat,from.lon,target.lat,target.lon))};
      remember(key,state,now);
    }
    let sample=target;
    if(Number.isFinite(state.correctionStarted)){
      const progress=clamp((now-state.correctionStarted)/state.correctionMs);
      sample=samplePath(path,state.fromFraction+(targetFraction-state.fromFraction)*ease(progress))||target;
      if(progress>=1){delete state.correctionStarted;delete state.correctionMs;delete state.fromFraction;}
    }
    state.displayed=sample;remember(key,state,now);
    return {...activity,lat:sample.lat,lon:sample.lon,bearing:sample.bearing,
      position_method:'estimated',position_label:`Estimated along the published railway path between confirmed reports • ${activity.position_label||''}`};
  }

  function aircraftPresentation(activity,now,reducedMotion){
    if(reducedMotion)return activity;
    const updated=Date.parse(activity.last_updated||''),speed=Number(activity.speed),bearing=Number(activity.bearing);
    const rawAge=(now-updated)/1000;
    if(!Number.isFinite(updated)||!Number.isFinite(speed)||!Number.isFinite(bearing)||rawAge<=0||speed<5||speed>800)return activity;
    // Freeze at the end of the safe dead-reckoning window. The previous code
    // jumped back to the old ADS-B coordinate as soon as 22 seconds elapsed.
    const age=Math.min(22,rawAge);
    const target=projectPosition(Number(activity.lat),Number(activity.lon),bearing,speed*1.852*age/3600);
    const key=stateKey(activity);
    const signature=`${activity.last_updated||''}|${activity.lat}|${activity.lon}|${bearing}|${speed}`;
    let state=motionStates.get(key);
    if(!state||state.kind!=='aircraft'){
      state=remember(key,{kind:'aircraft',signature,displayed:target},now);
    }else if(state.signature!==signature){
      const from=state.displayed||target;
      state={kind:'aircraft',signature,displayed:from,from,
        correctionStarted:now,correctionMs:Math.max(180,Math.min(900,180+distanceKm(from.lat,from.lon,target.lat,target.lon)*180))};
      remember(key,state,now);
    }
    let displayed=target;
    if(Number.isFinite(state.correctionStarted)){
      const progress=clamp((now-state.correctionStarted)/state.correctionMs),amount=ease(progress);
      displayed={lat:state.from.lat+(target.lat-state.from.lat)*amount,lon:state.from.lon+(target.lon-state.from.lon)*amount};
      if(progress>=1){delete state.correctionStarted;delete state.correctionMs;delete state.from;}
    }
    state.displayed=displayed;remember(key,state,now);
    return {...activity,...displayed,position_method:'estimated',position_label:`Live estimate from the latest ADS-B report • ${activity.position_label||''}`};
  }

  function present(activity,now=Date.now(),reducedMotion=false){
    if(!activity)return activity;
    if(activity.type==='train')return trainPresentation(activity,now,reducedMotion);
    if(['aircraft','sar'].includes(activity.type))return aircraftPresentation(activity,now,reducedMotion);
    return activity;
  }

  function reset(){motionStates.clear();lastPrune=0;}

  root.UKActivityMotionEngine={distanceKm,samplePath,nearestPathFraction,projectPosition,present,reset};
})(window);
