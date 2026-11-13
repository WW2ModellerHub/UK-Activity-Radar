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

  // Server-minus-device clock offset. Rail estimate windows are stamped on
  // the server clock; a device a few seconds fast or slow otherwise starts
  // every train late, runs it ahead, then yanks it back on the next report.
  let clockOffsetMs=0;
  function setServerTime(serverMs,receivedAt=Date.now()){
    const offset=Number(serverMs)-Number(receivedAt);
    if(!Number.isFinite(offset)||Math.abs(offset)>6*3600000)return clockOffsetMs;
    // Smooth network jitter; adopt large first corrections immediately.
    clockOffsetMs=Math.abs(offset-clockOffsetMs)>5000?offset:clockOffsetMs*.8+offset*.2;
    return clockOffsetMs;
  }
  function clockOffset(){return clockOffsetMs;}

  // An off-path correction (e.g. a report arriving without a track leg) this
  // short glides for under a second, which at map scale stays on the railway.
  // Anything further is placed directly rather than slid across countryside.
  const TRAIN_GLIDE_MAX_KM=1.5;
  // Corrections along the railway itself never leave the track, so they may
  // cover more ground before a direct placement is preferable.
  const TRAIN_PATH_GLIDE_MAX_KM=30;
  // A new report that puts the train slightly *behind* where it is drawn is
  // normal timetable/reporting noise. Hold position until the estimate catches
  // up instead of visibly reversing along the track.
  const TRAIN_HOLD_MAX_KM=1.5;
  // The drawn marker counts as "on" a new path within this distance.
  const TRAIN_ON_PATH_KM=.35;

  function trainTarget(activity,serverNow){
    const started=Date.parse(activity.estimate_started_at||''),due=Date.parse(activity.estimate_due_at||'');
    const path=usablePath(activity.estimate_path);
    if(path.length>=2&&Number.isFinite(started)&&Number.isFinite(due)&&due>started&&serverNow>=started){
      const fraction=clamp((serverNow-started)/(due-started));
      const sample=samplePath(path,fraction);
      if(sample)return {mode:'path',path,fraction,sample,
        key:`path|${activity.estimate_started_at||''}|${activity.estimate_due_at||''}|${JSON.stringify(path)}`};
    }
    const lat=Number(activity.lat),lon=Number(activity.lon);
    if(!Number.isFinite(lat)||!Number.isFinite(lon))return null;
    return {mode:'point',sample:{lat,lon,bearing:activity.bearing},key:`point|${lat.toFixed(6)}|${lon.toFixed(6)}`};
  }

  function beginTrainCorrection(state,target,now){
    const from=state.displayed;
    state.key=target.key;
    delete state.fromFraction;delete state.holdFraction;delete state.from;
    delete state.correctionStarted;delete state.correctionMs;
    if(!from)return;
    if(target.mode==='path'){
      const projected=nearestPathFraction(target.path,from.lat,from.lon);
      if(projected&&projected.distance<=TRAIN_ON_PATH_KM){
        const along=Math.abs(projected.fraction-target.fraction)*target.sample.total;
        if(projected.fraction>target.fraction&&along<=TRAIN_HOLD_MAX_KM){state.holdFraction=projected.fraction;return;}
        if(along>TRAIN_PATH_GLIDE_MAX_KM)return;
        state.fromFraction=projected.fraction;state.correctionStarted=now;state.correctionMs=correctionDuration(along);
        return;
      }
    }
    const gap=distanceKm(from.lat,from.lon,target.sample.lat,target.sample.lon);
    if(gap<.001||gap>TRAIN_GLIDE_MAX_KM)return;
    state.from={lat:from.lat,lon:from.lon,bearing:from.bearing};state.correctionStarted=now;state.correctionMs=correctionDuration(gap);
  }

  function trainPresentation(activity,now,reducedMotion){
    if(reducedMotion)return activity;
    const target=trainTarget(activity,now+clockOffsetMs);
    if(!target)return activity;
    const key=stateKey(activity);
    let state=motionStates.get(key);
    if(!state||state.kind!=='train'){
      state={kind:'train',key:target.key};
    }else if(state.key!==target.key){
      beginTrainCorrection(state,target,now);
    }
    let sample=target.sample;
    if(target.mode==='path'&&Number.isFinite(state.holdFraction)){
      if(target.fraction>=state.holdFraction)delete state.holdFraction;
      else sample=samplePath(target.path,state.holdFraction)||sample;
    }else if(Number.isFinite(state.correctionStarted)){
      const progress=clamp((now-state.correctionStarted)/state.correctionMs),amount=ease(progress);
      if(target.mode==='path'&&Number.isFinite(state.fromFraction)){
        sample=samplePath(target.path,state.fromFraction+(target.fraction-state.fromFraction)*amount)||sample;
      }else if(state.from){
        sample={...sample,lat:state.from.lat+(sample.lat-state.from.lat)*amount,lon:state.from.lon+(sample.lon-state.from.lon)*amount,
          bearing:Number.isFinite(Number(state.from.bearing))?state.from.bearing:sample.bearing};
      }
      if(progress>=1){delete state.correctionStarted;delete state.correctionMs;delete state.fromFraction;delete state.from;}
    }
    state.displayed={lat:sample.lat,lon:sample.lon,bearing:sample.bearing};remember(key,state,now);
    if(target.mode!=='path'){
      if(!Number.isFinite(state.correctionStarted))return activity;
      return {...activity,lat:sample.lat,lon:sample.lon};
    }
    return {...activity,lat:sample.lat,lon:sample.lon,bearing:sample.bearing,
      position_method:'estimated',position_label:`Estimated along the published railway path between confirmed reports • ${activity.position_label||''}`};
  }

  function aircraftPresentation(activity,now,reducedMotion){
    if(reducedMotion)return activity;
    const updated=Date.parse(activity.last_updated||''),speed=Number(activity.speed),bearing=Number(activity.bearing);
    const rawAge=(now+clockOffsetMs-updated)/1000;
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

  function reset(){motionStates.clear();lastPrune=0;clockOffsetMs=0;}

  root.UKActivityMotionEngine={distanceKm,samplePath,nearestPathFraction,projectPosition,present,reset,setServerTime,clockOffset};
})(window);
