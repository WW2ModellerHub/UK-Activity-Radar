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
  // A train catching up after a late report or a new timetable leg runs
  // visibly faster along the track - about 135 mph above its own pace - for
  // up to 25 s, instead of covering a kilometre or more in one second (user
  // screenshots, South Wigston: two smooth frames, then a 1.5 km leap).
  const TRAIN_CATCH_UP_KM_PER_S=.06;
  // Smoothstep: eases in and out with half the peak speed of ease() above.
  function gentle(value){const t=clamp(value);return t*t*(3-2*t);}
  function trainCatchUpMs(distance){
    return Math.max(1200,Math.min(25000,Math.max(0,distance)/TRAIN_CATCH_UP_KM_PER_S*1000));
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
  // Corrections up to this size glide (over trainCatchUpMs); bigger ones are a
  // different train or a reconnect and are taken at once. At 1.5 km, measured
  // 1.7-1.8 km corrections were snapping.
  const TRAIN_GLIDE_MAX_KM=6;
  // Corrections along the railway itself never leave the track, so they may
  // cover more ground before a direct placement is preferable.
  const TRAIN_PATH_GLIDE_MAX_KM=30;
  // A new report that puts the train slightly *behind* where it is drawn is
  // normal timetable/reporting noise. Hold position until the estimate catches
  // up instead of visibly reversing along the track.
  const TRAIN_HOLD_MAX_KM=1.5;
  // How far short of an unconfirmed stop a train waits (see trainTarget).
  const ARRIVAL_HOLD_KM=.2;
  // The drawn marker counts as "on" a new path within this distance.
  const TRAIN_ON_PATH_KM=.35;

  // Distance along a leg after `elapsed` of `total` seconds: speeding up from
  // a stand over `accel` s, braking to a stop over `brake` s, steady between
  // (Network Rail running times; app/running_profile.py does the same sum).
  function legFraction(elapsed,total,accel,brake){
    if(!(total>0))return 1;
    const t=Math.max(0,Math.min(total,elapsed));
    let a=Math.max(0,Number(accel)||0),b=Math.max(0,Number(brake)||0);
    if(a+b>=total)a=b=0;
    const speed=1/(total-a/2-b/2);
    if(a&&t<a)return speed*t*t/(2*a);
    if(b&&t>total-b){const left=total-t;return 1-speed*left*left/(2*b);}
    return speed*(t-a/2);
  }

  function trainTarget(activity,serverNow){
    const started=Date.parse(activity.estimate_started_at||''),due=Date.parse(activity.estimate_due_at||'');
    const path=usablePath(activity.estimate_path);
    if(path.length>=2&&Number.isFinite(started)&&Number.isFinite(due)&&due>started&&serverNow>=started){
      let fraction=clamp(legFraction((serverNow-started)/1000,(due-started)/1000,activity.estimate_accel_s,activity.estimate_brake_s));
      let sample=samplePath(path,fraction);
      // A train is not drawn at its next stop until a report says it got
      // there: on time by the timetable is not arrived. It waits ~200 m out.
      // (An arrival leg from a signalling step already knows when it arrives.)
      // Not past the next signal berth until the train steps into it (learned
      // on the server; only on the leg it was set for, and only for 3 minutes).
      const cap=activity.estimate_cap;
      if(Array.isArray(cap)&&cap.length>=6&&serverNow<=cap[5]&&fraction>cap[4]){
        const first=path[0],last=path[path.length-1];
        if(Math.abs(first[0]-cap[0])<1e-4&&Math.abs(first[1]-cap[1])<1e-4&&Math.abs(last[0]-cap[2])<1e-4&&Math.abs(last[1]-cap[3])<1e-4){
          fraction=cap[4];sample=samplePath(path,fraction)||sample;
        }
      }
      if(sample&&Number(activity.estimate_brake_s)>0&&activity.rail_event_type!=='Arrival'){
        const cap=Math.max(0,1-ARRIVAL_HOLD_KM/Math.max(sample.total,ARRIVAL_HOLD_KM*2));
        if(fraction>cap){fraction=cap;sample=samplePath(path,cap);}
      }
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
        state.fromFraction=projected.fraction;state.correctionStarted=now;state.correctionMs=trainCatchUpMs(along);
        return;
      }
    }
    const gap=distanceKm(from.lat,from.lon,target.sample.lat,target.sample.lon);
    if(gap<.001||gap>TRAIN_GLIDE_MAX_KM)return;
    state.from={lat:from.lat,lon:from.lon,bearing:from.bearing};state.correctionStarted=now;state.correctionMs=trainCatchUpMs(gap);
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
      const progress=clamp((now-state.correctionStarted)/state.correctionMs),amount=gentle(progress);
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

  // When a new report moves a vehicle's position, the jump is not shown:
  // the difference is blended away over a few seconds, so nothing snaps back.
  // A jump too big to be a correction (a reconnect) is taken at once.
  const BLEND_MS={aircraft:2500,bus:3000};
  const BLEND_MAX_KM={aircraft:8,bus:1};
  function startBlend(state,raw,now,kind){
    const from=state.displayed;
    if(!from||!raw)return;
    const gap=distanceKm(from.lat,from.lon,raw.lat,raw.lon);
    if(gap<.001||gap>BLEND_MAX_KM[kind]){delete state.offset;return;}
    state.offset={lat:from.lat-raw.lat,lon:from.lon-raw.lon};state.offsetStarted=now;
    // A bigger correction (a plane that turned during a long gap) eases over longer, up to 8 s.
    state.offsetMs=Math.max(BLEND_MS[kind],Math.min(8000,gap*1500));
  }
  function blended(state,raw,now,kind){
    if(!state.offset)return raw;
    const progress=clamp((now-state.offsetStarted)/(state.offsetMs||BLEND_MS[kind]));
    if(progress>=1){delete state.offset;delete state.offsetStarted;return raw;}
    const left=1-ease(progress);
    return {lat:raw.lat+state.offset.lat*left,lon:raw.lon+state.offset.lon*left};
  }

  // Aircraft are shown a few seconds behind real time and moved between their
  // two latest real reports, so they never overshoot and get pulled back:
  // the "entity interpolation" games use, and what FlightAware's maps do. The
  // delay is the plane's usual gap between reports, at most 8 s. Past its
  // newest report a plane carries on along its track and speed for up to 22 s.
  const AIRCRAFT_DELAY_MAX_MS=8000;
  function aircraftReport(activity){
    return {t:Date.parse(activity.last_updated||''),lat:Number(activity.lat),lon:Number(activity.lon),
      speed:Number(activity.speed),bearing:Number(activity.bearing)};
  }
  // 1.5x the plane's usual gap between reports, 22-150 s; 90 s before there is a gap to go on.
  function aircraftProjectionLimitS(reports){
    if(reports.length<2)return 90;
    const gaps=[];
    for(let index=1;index<reports.length;index+=1)gaps.push((reports[index].t-reports[index-1].t)/1000);
    gaps.sort((a,b)=>a-b);
    return Math.max(22,Math.min(150,1.5*gaps[Math.floor(gaps.length/2)]));
  }
  function aircraftRaw(reports,serverNow){
    const newest=reports[reports.length-1];
    let delay=0;
    if(reports.length>1){
      const gaps=[];
      for(let index=1;index<reports.length;index+=1)gaps.push(reports[index].t-reports[index-1].t);
      gaps.sort((a,b)=>a-b);
      delay=Math.min(AIRCRAFT_DELAY_MAX_MS,Math.max(0,gaps[Math.floor(gaps.length/2)]));
    }
    const at=serverNow-delay;
    for(let index=reports.length-1;index>0;index-=1){
      const a=reports[index-1],b=reports[index];
      if(at>=a.t&&at<=b.t&&b.t>a.t){
        const share=(at-a.t)/(b.t-a.t);
        return {lat:a.lat+(b.lat-a.lat)*share,lon:a.lon+(b.lon-a.lon)*share};
      }
    }
    if(at<reports[0].t)return {lat:reports[0].lat,lon:reports[0].lon};
    // Carry on along the track for as long as this plane's reports are apart:
    // the background feed reports each plane about every 60 s (up to 180 s),
    // and a 22 s limit left them frozen for most of each gap, then jumping.
    const age=Math.min(aircraftProjectionLimitS(reports),(at-newest.t)/1000);
    if(age>0&&Number.isFinite(newest.speed)&&Number.isFinite(newest.bearing)&&newest.speed>=5&&newest.speed<=800){
      return projectPosition(newest.lat,newest.lon,newest.bearing,newest.speed*1.852*age/3600);
    }
    return {lat:newest.lat,lon:newest.lon};
  }
  function aircraftPresentation(activity,now,reducedMotion){
    if(reducedMotion)return activity;
    const report=aircraftReport(activity);
    if(!Number.isFinite(report.t)||!Number.isFinite(report.lat)||!Number.isFinite(report.lon))return activity;
    const key=stateKey(activity),serverNow=now+clockOffsetMs;
    let state=motionStates.get(key);
    if(!state||state.kind!=='aircraft')state={kind:'aircraft',reports:[]};
    const newest=state.reports[state.reports.length-1];
    if(!newest||report.t>newest.t||(report.t===newest.t&&(report.lat!==newest.lat||report.lon!==newest.lon))){
      if(newest&&report.t===newest.t)state.reports.pop();
      state.reports.push(report);
      if(state.reports.length>6)state.reports.shift();
      startBlend(state,aircraftRaw(state.reports,serverNow),now,'aircraft');
    }
    const displayed=blended(state,aircraftRaw(state.reports,serverNow),now,'aircraft');
    state.displayed=displayed;remember(key,state,now);
    return {...activity,...displayed,position_method:'estimated',position_label:`Live estimate between ADS-B reports • ${activity.position_label||''}`};
  }

  // Bus route lines (from the bus's timetabled trip), by bus. With one, a
  // bus is snapped onto its road and moved along it, as live bus maps do,
  // rather than in a straight line that cuts corners.
  const busShapes=new Map();
  function setBusShape(id,points){
    const usable=usablePath(points);
    if(usable.length<2){busShapes.delete(`bus:${id}`);return;}
    const cumulative=[0];
    for(let index=1;index<usable.length;index+=1)cumulative.push(cumulative[index-1]+distanceKm(...usable[index-1],...usable[index]));
    busShapes.set(`bus:${id}`,{points:usable,cumulative,total:cumulative[cumulative.length-1]});
  }
  // Distance along the line (km) of the point on it nearest to lat/lon, and how far off the line that is.
  function alongShape(shape,lat,lon){
    const scale=Math.cos(radians(lat));let best=null;
    for(let index=1;index<shape.points.length;index+=1){
      const [aLat,aLon]=shape.points[index-1],[bLat,bLon]=shape.points[index];
      const ax=(aLon-lon)*scale,ay=aLat-lat,bx=(bLon-lon)*scale,by=bLat-lat;
      const dx=bx-ax,dy=by-ay,length=dx*dx+dy*dy;
      const share=length>0?clamp(-(ax*dx+ay*dy)/length):0;
      const px=ax+dx*share,py=ay+dy*share,offDegrees=Math.sqrt(px*px+py*py);
      if(!best||offDegrees<best.off)best={off:offDegrees,along:shape.cumulative[index-1]+(shape.cumulative[index]-shape.cumulative[index-1])*share};
    }
    return best?{along:best.along,offKm:best.off*111.32}:null;
  }
  function pointOnShape(shape,along){
    const target=Math.max(0,Math.min(shape.total,along));
    let index=1;
    while(index<shape.cumulative.length-1&&shape.cumulative[index]<target)index+=1;
    const from=shape.points[index-1],to=shape.points[index],span=shape.cumulative[index]-shape.cumulative[index-1];
    const share=span>0?(target-shape.cumulative[index-1])/span:0;
    const dLon=radians(to[1]-from[1]),y=Math.sin(dLon)*Math.cos(radians(to[0]));
    const x=Math.cos(radians(from[0]))*Math.sin(radians(to[0]))-Math.sin(radians(from[0]))*Math.cos(radians(to[0]))*Math.cos(dLon);
    return {lat:from[0]+(to[0]-from[0])*share,lon:from[1]+(to[1]-from[1])*share,bearing:(Math.atan2(y,x)*180/Math.PI+360)%360};
  }

  function busOnShape(activity,shape,now,state){
    const report=alongShape(shape,Number(activity.lat),Number(activity.lon));
    if(!report||report.offKm>.08){
      // Not on this line: a new trip, a diversion or a wrong match. Drop it;
      // the map asks for the current one again later.
      busShapes.delete(stateKey(activity));
      return null;
    }
    const updated=Date.parse(activity.last_updated||''),rawAge=Math.max(0,(now+clockOffsetMs-updated)/1000);
    const signature=`${activity.last_updated||''}|${activity.lat}|${activity.lon}`;
    if(state.shapeSignature!==signature){
      // Speed along the road: the reported one, else from the last two reports.
      const previous=state.shapeReport;
      state.shapeReport={t:updated,along:report.along};
      if(previous&&updated>previous.t){
        const seconds=(updated-previous.t)/1000,metresPerSecond=(report.along-previous.along)*1000/seconds;
        if(seconds>=5&&seconds<=180&&metresPerSecond>=0&&metresPerSecond<=25)state.shapeSpeed=metresPerSecond;
      }
      const reported=Number(activity.speed);
      if(Number.isFinite(reported)&&activity.speed!=null)state.shapeSpeed=reported<.7?0:Math.min(25,reported);
      state.shapeSignature=signature;
      state.shapeNew=true;
    }
    const speed=Number.isFinite(state.shapeSpeed)?state.shapeSpeed:0;
    // Carry on along the road for up to 30 s (about one report), then wait.
    const raw=Math.min(shape.total,report.along+speed*Math.min(30,rawAge)/1000);
    if(state.shapeNew){
      state.shapeNew=false;
      const shown=state.shapeShown;
      delete state.shapeHold;delete state.shapeOffset;
      if(Number.isFinite(shown)){
        const gap=shown-raw;
        // Ahead of the new report by a little: wait there rather than reverse.
        if(gap>0&&gap<.3)state.shapeHold=shown;
        else if(Math.abs(gap)<1){state.shapeOffset=gap;state.shapeOffsetStarted=now;}
      }
    }
    let along=raw;
    if(Number.isFinite(state.shapeHold)){
      if(raw>=state.shapeHold)delete state.shapeHold;else along=state.shapeHold;
    }else if(Number.isFinite(state.shapeOffset)){
      const progress=clamp((now-state.shapeOffsetStarted)/BLEND_MS.bus);
      if(progress>=1)delete state.shapeOffset;else along=raw+state.shapeOffset*(1-ease(progress));
    }
    state.shapeShown=along;
    return pointOnShape(shape,along);
  }

  function busPresentation(activity,now,reducedMotion){
    if(reducedMotion)return activity;
    const updated=Date.parse(activity.last_updated||''),speed=Number(activity.speed),bearing=Number(activity.bearing);
    const rawAge=(now+clockOffsetMs-updated)/1000;
    if(!Number.isFinite(updated)||!Number.isFinite(Number(activity.lat))||!Number.isFinite(Number(activity.lon)))return activity;
    const key=stateKey(activity);
    let state=motionStates.get(key);
    if(!state||state.kind!=='bus')state={kind:'bus'};
    const shape=busShapes.get(key);
    const onRoad=shape?busOnShape(activity,shape,now,state):null;
    if(onRoad){
      state.displayed={lat:onRoad.lat,lon:onRoad.lon};remember(key,state,now);
      return {...activity,lat:onRoad.lat,lon:onRoad.lon,bearing:onRoad.bearing,position_method:'estimated',
        position_label:`Live estimate along its route from the latest BODS GPS report • ${activity.position_label||''}`};
    }
    // No route line: BODS publishes metres/second. Buses report only every
    // 10-30 s, too far apart to show them that far behind, so they carry on
    // along their last heading for one reporting interval (18 s) and hold.
    // A bus stopped (at a stop, in traffic) stays at its report, but still
    // through the blend: it eases back rather than snapping there.
    const moving=Number.isFinite(speed)&&Number.isFinite(bearing)&&rawAge>0&&speed>=.7&&speed<=45;
    const raw=moving?projectPosition(Number(activity.lat),Number(activity.lon),bearing,speed*Math.min(18,rawAge)/1000)
      :{lat:Number(activity.lat),lon:Number(activity.lon)};
    const signature=`${activity.last_updated||''}|${activity.lat}|${activity.lon}|${bearing}|${speed}`;
    if(state.signature===undefined)state.signature=signature;
    else if(state.signature!==signature){state.signature=signature;startBlend(state,raw,now,'bus');}
    const displayed=blended(state,raw,now,'bus');
    state.displayed=displayed;remember(key,state,now);
    return {...activity,...displayed,position_method:'estimated',position_label:`Live estimate from the latest BODS GPS report • ${activity.position_label||''}`};
  }

  function present(activity,now=Date.now(),reducedMotion=false){
    if(!activity)return activity;
    if(activity.type==='train')return trainPresentation(activity,now,reducedMotion);
    if(activity.type==='bus')return busPresentation(activity,now,reducedMotion);
    if(['aircraft','sar'].includes(activity.type))return aircraftPresentation(activity,now,reducedMotion);
    return activity;
  }

  function reset(){motionStates.clear();busShapes.clear();lastPrune=0;clockOffsetMs=0;}

  root.UKActivityMotionEngine={distanceKm,samplePath,legFraction,nearestPathFraction,projectPosition,present,reset,setServerTime,clockOffset,setBusShape,hasBusShape:id=>busShapes.has(`bus:${id}`)};
})(window);
