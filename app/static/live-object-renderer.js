import * as maplibregl from './vendor/maplibre/maplibre-gl.mjs';

const MOVING_TYPES=new Set(['train','aircraft','sar']);

// Leaflet's zoom levels are for 256px tiles and MapLibre's for 512px tiles, so
// the same map scale is one MapLibre zoom level lower. Without this the live
// layer drew every train and aircraft twice as far from the centre as the map.
export function maplibreZoom(leafletZoom){return Number(leafletZoom)-1;}
// Leaflet animates zooms with transform .25s cubic-bezier(0,0,.25,1).
function leafletZoomEasing(t){return 1-Math.pow(1-t,3);}

export function frameInterval(objectCount,selected=false,reducedMotion=false){
  if(reducedMotion)return 100;
  if(selected||objectCount<=120)return 16;
  if(objectCount<=600)return 33;
  return 50;
}

function punctuality(activity){
  if(activity.type!=='train')return {};
  if(activity.cancelled_at)return {punctuality:'cancelled'};
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

// Same top-down aircraft shapes as the Leaflet markers (24px grid, north up):
// [filled body paths, rotor/propeller strokes, faint rotor discs].
const AIRCRAFT_PATHS={
  airliner:[['M12 1.5c.9 0 1.5 1 1.5 2.4V9l8 4.6v2.1l-8-2.4v5.2l2.4 1.8v1.6L12 21l-3.9.9v-1.6l2.4-1.8v-5.2l-8 2.4v-2.1l8-4.6V3.9c0-1.4.6-2.4 1.5-2.4z'],[],[]],
  heavy:[['M12 1c1 0 1.6 1.1 1.6 2.6V8.6l9.4 5v2.2l-9.4-2.6v5.6l2.6 1.8v1.7L12 21.4l-4.2.9v-1.7l2.6-1.8v-5.6L1 15.8v-2.2l9.4-5V3.6C10.4 2.1 11 1 12 1z','M5.2 12.6h1.8v2.6H5.2zM8 11.1h1.8v2.6H8zM14.2 11.1H16v2.6h-1.8zM17 12.6h1.8v2.6H17z'],[],[]],
  fastjet:[['M12 1l1.6 5 .4 5 8 6v2.2l-8-1.6-.6 2.6 3 2v1.6L12 22.2l-4.4.6v-1.6l3-2-.6-2.6-8 1.6V17l8-6 .4-5z'],[],[]],
  propeller:[['M12 4c.8 0 1.3.8 1.3 2v3.6l8.7 1.2v2.2l-8.7.6-.4 5.6 2.6 1.4v1.6L12 21.6l-3.5.6v-1.6l2.6-1.4-.4-5.6-8.7-.6v-2.2l8.7-1.2V6c0-1.2.5-2 1.3-2z'],['M8.4 3h7.2'],[]],
  helicopter:[['M15.8 10a3.8 5.6 0 1 1-7.6 0a3.8 5.6 0 1 1 7.6 0z','M11.2 14.5h1.6v6h-1.6z','M8.8 19.8h6.4v1.8H8.8z'],['M3 2.5l18 15M21 2.5l-18 15'],[]],
};
const AIRCRAFT_COLOURS={airliner:'#0b63ce',heavy:'#6d4fd8',fastjet:'#b26a00',propeller:'#0f8a7a',helicopter:'#d64545'};

function drawIcon(kind,size=64){
  const canvas=document.createElement('canvas');canvas.width=size;canvas.height=size;
  const context=canvas.getContext('2d');
  context.lineJoin='round';context.lineCap='round';
  if(kind==='train'){
    context.translate(size/2,size/2);
    context.fillStyle='#7b2d70';context.strokeStyle='#10212d';context.lineWidth=3;
    context.beginPath();context.roundRect(-17,-21,34,39,10);context.fill();context.stroke();
    context.fillStyle='#dff7ff';context.fillRect(-11,-13,22,10);
    context.strokeStyle='#fff';context.beginPath();context.moveTo(-13,24);context.lineTo(-6,16);context.moveTo(13,24);context.lineTo(6,16);context.stroke();
    return context.getImageData(0,0,size,size);
  }
  const [bodies,rotors,discs]=AIRCRAFT_PATHS[kind]||AIRCRAFT_PATHS.airliner;
  const colour=AIRCRAFT_COLOURS[kind]||AIRCRAFT_COLOURS.airliner;
  const scale=size/24*.82;
  context.translate(size/2-12*scale,size/2-12*scale);context.scale(scale,scale);
  context.fillStyle=colour;context.strokeStyle=colour;
  for(const [cx,cy,r] of discs){context.globalAlpha=.1;context.beginPath();context.arc(cx,cy,r,0,Math.PI*2);context.fill();context.globalAlpha=.45;context.lineWidth=.8;context.stroke();}
  context.globalAlpha=.7;context.lineWidth=1.6;
  for(const path of rotors)context.stroke(new Path2D(path));
  context.globalAlpha=1;
  for(const path of bodies)context.fill(new Path2D(path));
  return context.getImageData(0,0,size,size);
}

export class LiveObjectRenderer{
  constructor({container,leafMap,motionEngine,onReady}){
    this.container=container;this.leafMap=leafMap;this.motionEngine=motionEngine;this.rows=[];this.selectedId=null;this.ready=false;this.frame=0;this.syncFrame=0;this.lastDraw=0;this.lastFeatures=[];
    const center=leafMap.getCenter();
    this.map=new maplibregl.Map({container,interactive:false,attributionControl:false,antialias:true,renderWorldCopies:true,
      style:{version:8,sources:{},layers:[]},center:[center.lng,center.lat],zoom:maplibreZoom(leafMap.getZoom())});
    // Follow the base map in the same frame it moves, so live objects stay
    // fixed to the tracks while dragging rather than trailing by a frame.
    this.sync=()=>{
      const value=leafMap.getCenter();
      this.map.jumpTo({center:[value.lng,value.lat],zoom:maplibreZoom(leafMap.getZoom()),bearing:0,pitch:0});
    };
    this.resize=()=>{this.map.resize();this.sync();};
    // Button, wheel and double-tap zooms are CSS animations in Leaflet that
    // only report the new zoom at the end. Run the same animation here so
    // objects scale with the map instead of jumping when it finishes.
    this.zoomAnim=event=>{
      if(!this.ready||!event?.center)return;
      this.map.easeTo({center:[event.center.lng,event.center.lat],zoom:maplibreZoom(event.zoom),duration:250,easing:leafletZoomEasing,essential:true});
    };
    leafMap.on('move zoom',this.sync);
    leafMap.on('resize',this.resize);
    leafMap.on('zoomanim',this.zoomAnim);
    this.map.on('load',()=>{
      for(const kind of ['train','airliner','propeller','helicopter','fastjet','heavy'])this.map.addImage(`live-${kind}`,drawIcon(kind),{pixelRatio:2});
      this.map.addSource('live-objects',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
      this.map.addLayer({id:'live-object-halo',type:'circle',source:'live-objects',paint:{'circle-radius':['case',['get','selected'],23,['==',['get','type'],'train'],19,16],'circle-color':['case',['==',['get','type'],'train'],'rgba(77,22,69,.88)','#ffffff'],'circle-stroke-width':['case',['get','selected'],4,2],'circle-stroke-color':['case',['get','selected'],'#ffe27a',['==',['get','type'],'train'],'#effaff',['match',['get','icon'],'live-helicopter','#d64545','live-heavy','#6d4fd8','live-fastjet','#b26a00','live-propeller','#0f8a7a','#0b63ce']],'circle-blur':.12}});
      this.map.addLayer({id:'live-object-punctuality',type:'circle',source:'live-objects',filter:['has','punctuality'],paint:{'circle-radius':5,'circle-translate':[11,-11],'circle-color':['match',['get','punctuality'],'on-time','#1e9e6a','late','#d4a017','cancelled','#6b7280','#d64545'],'circle-stroke-width':2,'circle-stroke-color':'#ffffff'}});
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
          const metresPerPixel=78271.51696*Math.cos(lat*Math.PI/180)/(2**zoom);
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
  destroy(){cancelAnimationFrame(this.frame);this.leafMap.off('move zoom',this.sync);this.leafMap.off('resize',this.resize);this.leafMap.off('zoomanim',this.zoomAnim);this.map.remove();this.container.closest('.map-panel')?.classList.remove('maplibre-live-ready');}
}
