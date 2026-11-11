(function(root){
  'use strict';

  const clamp=value=>Math.max(0,Math.min(1,Number(value)||0));
  const radians=value=>Number(value)*Math.PI/180;

  function distanceKm(lat1,lon1,lat2,lon2){
    const radius=6371;
    const dLat=radians(lat2-lat1),dLon=radians(lon2-lon1);
    const a=Math.sin(dLat/2)**2+Math.cos(radians(lat1))*Math.cos(radians(lat2))*Math.sin(dLon/2)**2;
    return radius*2*Math.atan2(Math.sqrt(a),Math.sqrt(1-a));
  }

  function samplePath(points,fraction){
    const usable=(Array.isArray(points)?points:[])
      .map(point=>[Number(point?.[0]),Number(point?.[1])])
      .filter(point=>Number.isFinite(point[0])&&Number.isFinite(point[1]));
    if(usable.length<2)return null;
    const legs=[];let total=0;
    for(let index=1;index<usable.length;index+=1){
      const length=distanceKm(...usable[index-1],...usable[index]);
      if(!Number.isFinite(length)||length<=0)continue;
      legs.push({from:usable[index-1],to:usable[index],length,start:total});total+=length;
    }
    if(!legs.length)return null;
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
    return {lat,lon,bearing,total,progress,points:usable};
  }

  function projectPosition(lat,lon,bearing,distance){
    const angular=distance/6371,heading=radians(bearing),p1=radians(lat),l1=radians(lon);
    const p2=Math.asin(Math.sin(p1)*Math.cos(angular)+Math.cos(p1)*Math.sin(angular)*Math.cos(heading));
    const l2=l1+Math.atan2(Math.sin(heading)*Math.sin(angular)*Math.cos(p1),Math.cos(angular)-Math.sin(p1)*Math.sin(p2));
    return {lat:p2*180/Math.PI,lon:((l2*180/Math.PI+540)%360)-180};
  }

  function present(activity,now=Date.now(),reducedMotion=false){
    if(!activity)return activity;
    if(activity.type==='train'){
      const started=Date.parse(activity.estimate_started_at||''),due=Date.parse(activity.estimate_due_at||'');
      if(Number.isFinite(started)&&Number.isFinite(due)&&due>started&&now>=started){
        const path=Array.isArray(activity.estimate_path)&&activity.estimate_path.length>=2
          ? activity.estimate_path
          : [[activity.reported_lat,activity.reported_lon],[activity.next_lat,activity.next_lon]];
        const sample=samplePath(path,(now-started)/(due-started));
        if(sample)return {...activity,lat:sample.lat,lon:sample.lon,bearing:sample.bearing,
          position_method:'estimated',position_label:`Estimated along the published railway path between confirmed reports • ${activity.position_label||''}`};
      }
    }
    if(reducedMotion)return activity;
    if(['aircraft','sar'].includes(activity.type)){
      const updated=Date.parse(activity.last_updated||''),speed=Number(activity.speed),bearing=Number(activity.bearing);
      const age=(now-updated)/1000;
      if(Number.isFinite(updated)&&Number.isFinite(speed)&&Number.isFinite(bearing)&&age>0&&age<=22&&speed>=5&&speed<=800){
        const projected=projectPosition(Number(activity.lat),Number(activity.lon),bearing,speed*1.852*age/3600);
        return {...activity,...projected,position_method:'estimated',position_label:`Live estimate from the latest ADS-B report • ${activity.position_label||''}`};
      }
    }
    return activity;
  }

  root.UKActivityMotionEngine={distanceKm,samplePath,projectPosition,present};
})(window);
