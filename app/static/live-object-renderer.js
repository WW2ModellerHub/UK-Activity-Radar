import * as maplibregl from './vendor/maplibre/maplibre-gl.mjs';

const MOVING_TYPES=new Set(['train','aircraft','sar']);

export function frameInterval(objectCount,selected=false,reducedMotion=false){
  if(reducedMotion)return 100;
  if(selected||objectCount<=120)return 16;
  if(objectCount<=600)return 33;
  return 50;
}

function punctuality(activity){
  if(activity.type!=='train')return {};
  const delay=activity.delay_minutes;
  if(delay===null||delay===undefined||!Number.isFinite(Number(delay)))return {};
  const minutes=Number(delay);
  return {punctuality:minutes<=1?'on-time':minutes<=5?'late':'very-late'};
}

function iconKind(activity){
  if(activity.type==='train')return 'train';
  const text=`${activity.aircraft_model||''} ${activity.aircraft_type_code||''} ${activity.kind||''}`.toLowerCase();
  if(activity.type==='sar'||/(helicopter|lynx|chinook|apache|merlin|puma|s92|h135|ec135|aw1|bell|robinson)/.test(text))return 'helicopter';
  if(/(typhoon|fighter|hawk|f-?\d|fast jet)/.test(text))return 'fastjet';
  if(/(c-?17|a400|atlas|hercules|transporter|kc-?135|voyager)/.test(text))return 'heavy';
  if(/(cessna|c152|c172|diamond|piper|spitfire|prop)/.test(text))return 'propeller';
  return 'airliner';
}

function drawIcon(kind,size=64){
  const canvas=document.createElement('canvas');canvas.width=size;canvas.height=size;
  const context=canvas.getContext('2d');
  const train=kind==='train',helicopter=kind==='helicopter';
  context.translate(size/2,size/2);
  context.fillStyle=train?'#7b2d70':helicopter?'#ec786b':kind==='fastjet'?'#a995ff':kind==='heavy'?'#8584e8':'#58b8ef';
  context.strokeStyle='#10212d';context.lineWidth=3;context.lineJoin='round';context.lineCap='round';
  context.beginPath();
  if(train){
    context.roundRect(-17,-21,34,39,10);context.fill();context.stroke();
    context.fillStyle='#dff7ff';context.fillRect(-11,-13,22,10);
    context.strokeStyle='#fff';context.beginPath();context.moveTo(-13,24);context.lineTo(-6,16);context.moveTo(13,24);context.lineTo(6,16);context.stroke();
  }else if(helicopter){
    context.ellipse(3,4,17,9,0,0,Math.PI*2);context.fill();context.stroke();
    context.beginPath();context.moveTo(-13,3);context.lineTo(-27,-8);context.lineTo(-26,9);context.closePath();context.fill();context.stroke();
    context.beginPath();context.moveTo(-25,-17);context.lineTo(25,-17);context.moveTo(0,-17);context.lineTo(0,-5);context.stroke();
  }else{
    context.beginPath();context.moveTo(0,-28);context.lineTo(7,-7);context.lineTo(kind==='heavy'?27:24,4);context.lineTo(25,11);context.lineTo(6,5);context.lineTo(5,19);context.lineTo(13,25);context.lineTo(10,29);context.lineTo(0,25);context.lineTo(-10,29);context.lineTo(-13,25);context.lineTo(-5,19);context.lineTo(-6,5);context.lineTo(-25,11);context.lineTo(-27,4);context.lineTo(-7,-7);context.closePath();context.fill();context.stroke();
  }
  return context.getImageData(0,0,size,size);
}

export class LiveObjectRenderer{
  constructor({container,leafMap,motionEngine,onReady}){
    this.container=container;this.leafMap=leafMap;this.motionEngine=motionEngine;this.rows=[];this.selectedId=null;this.ready=false;this.frame=0;this.syncFrame=0;this.lastDraw=0;this.lastFeatures=[];
    const center=leafMap.getCenter();
    this.map=new maplibregl.Map({container,interactive:false,attributionControl:false,antialias:true,renderWorldCopies:true,
      style:{version:8,sources:{},layers:[]},center:[center.lng,center.lat],zoom:leafMap.getZoom()});
    this.sync=()=>{
      if(this.syncFrame)return;
      this.syncFrame=requestAnimationFrame(()=>{
        this.syncFrame=0;
        const value=leafMap.getCenter();
        this.map.resize();
        this.map.jumpTo({center:[value.lng,value.lat],zoom:leafMap.getZoom(),bearing:0,pitch:0});
      });
    };
    leafMap.on('move zoom resize',this.sync);
    this.map.on('load',()=>{
      for(const kind of ['train','airliner','propeller','helicopter','fastjet','heavy'])this.map.addImage(`live-${kind}`,drawIcon(kind),{pixelRatio:2});
      this.map.addSource('live-objects',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
      this.map.addLayer({id:'live-object-halo',type:'circle',source:'live-objects',paint:{'circle-radius':['case',['get','selected'],23,19],'circle-color':['case',['==',['get','type'],'train'],'rgba(77,22,69,.88)','rgba(8,29,42,.82)'],'circle-stroke-width':['case',['get','selected'],4,2],'circle-stroke-color':['case',['get','selected'],'#ffe27a','#effaff'],'circle-blur':.12}});
      this.map.addLayer({id:'live-object-punctuality',type:'circle',source:'live-objects',filter:['has','punctuality'],paint:{'circle-radius':5,'circle-translate':[11,-11],'circle-color':['match',['get','punctuality'],'on-time','#1e9e6a','late','#d4a017','#d64545'],'circle-stroke-width':2,'circle-stroke-color':'#ffffff'}});
      this.map.addLayer({id:'live-object-symbol',type:'symbol',source:'live-objects',layout:{'icon-image':['get','icon'],'icon-size':.52,'icon-allow-overlap':true,'icon-ignore-placement':true,'icon-rotate':['get','rotation'],'icon-rotation-alignment':'map'}});
      this.ready=true;this.container.closest('.map-panel')?.classList.add('maplibre-live-ready');this.container.dataset.renderer='ready';onReady?.();this.start();
    });
    this.map.on('error',event=>{
      if(!this.ready){this.container.dataset.renderer='fallback';this.container.closest('.map-panel')?.classList.remove('maplibre-live-ready');}
      console.warn(this.ready?'Live renderer warning':'Live renderer fallback',event?.error||event);
    });
  }
  setActivities(rows,selectedId){this.rows=(rows||[]).filter(row=>MOVING_TYPES.has(row?.type));this.selectedId=selectedId||null;if(this.ready)this.draw(performance.now(),true);}
  featureAt(point){
    if(!this.ready||!point)return null;
    const x=Number(point.x),y=Number(point.y);
    const hits=this.map.queryRenderedFeatures([[x-14,y-14],[x+14,y+14]],{layers:['live-object-symbol','live-object-halo']});
    if(hits[0]?.properties?.id)return hits[0].properties.id;
    // A source update can land between MapLibre render passes. Keep taps
    // responsive during that tiny window by hit-testing the same displayed
    // feature coordinates already used for the current frame.
    let nearest=null;
    for(const feature of this.lastFeatures){
      const screen=this.map.project(feature.geometry.coordinates);
      const distance=Math.hypot(Number(screen.x)-x,Number(screen.y)-y);
      if(distance<=28&&(!nearest||distance<nearest.distance))nearest={distance,id:feature.properties.id};
    }
    return nearest?.id||null;
  }
  draw(timestamp,force=false){
    if(!this.ready)return;
    const reduced=window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches;
    if(!force&&timestamp-this.lastDraw<frameInterval(this.rows.length,Boolean(this.selectedId),reduced))return;
    this.lastDraw=timestamp;const now=Date.now();
    const displayed=this.rows.map(row=>this.motionEngine.present(row,now,reduced)).filter(row=>Number.isFinite(Number(row.lat))&&Number.isFinite(Number(row.lon)));
    const groups=new Map();
    for(const row of displayed){
      const key=`${Number(row.lat).toFixed(5)}|${Number(row.lon).toFixed(5)}`;
      if(!groups.has(key))groups.set(key,[]);groups.get(key).push(row);
    }
    const zoom=this.map.getZoom();
    const features=[];
    let coincidentGroups=0,separatedObjects=0;
    for(const group of groups.values()){
      if(group.length>1){coincidentGroups+=1;separatedObjects+=group.length;}
      group.forEach((row,index)=>{
        let lat=Number(row.lat),lon=Number(row.lon);
        if(group.length>1){
          const angle=-Math.PI/2+Math.PI*2*index/group.length;
          const metresPerPixel=156543.03392*Math.cos(lat*Math.PI/180)/(2**zoom);
          const radiusMetres=Math.min(32,18+group.length*2)*metresPerPixel;
          lat+=Math.sin(angle)*radiusMetres/111320;
          lon+=Math.cos(angle)*radiusMetres/(111320*Math.max(.2,Math.cos(lat*Math.PI/180)));
        }
        features.push({type:'Feature',id:String(row.id),geometry:{type:'Point',coordinates:[lon,lat]},properties:{id:String(row.id),type:row.type,selected:row.id===this.selectedId,icon:`live-${iconKind(row)}`,rotation:Number(row.bearing||0),...punctuality(row)}});
      });
    }
    this.lastFeatures=features;
    this.map.getSource('live-objects')?.setData({type:'FeatureCollection',features});
    this.container.dataset.objectCount=String(features.length);
    this.container.dataset.coincidentGroups=String(coincidentGroups);
    this.container.dataset.separatedObjects=String(separatedObjects);
  }
  start(){const tick=time=>{this.draw(time);this.frame=requestAnimationFrame(tick);};this.frame=requestAnimationFrame(tick);}
  destroy(){cancelAnimationFrame(this.frame);cancelAnimationFrame(this.syncFrame);this.leafMap.off('move zoom resize',this.sync);this.map.remove();this.container.closest('.map-panel')?.classList.remove('maplibre-live-ready');}
}
