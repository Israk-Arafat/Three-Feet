import { useEffect, useState } from "react";
import { api } from "../lib/api";
import type { ViewGeometry } from "../types";
import { Icon } from "./Icon";

export function LiveView({cameraHealthy,simulated,calibrated,setupRevision,onSetup,className=""}:{cameraHealthy:boolean;simulated:boolean;calibrated:boolean;setupRevision:number;onSetup:()=>void;className?:string}) {
  const [attempt,setAttempt]=useState(0),[failed,setFailed]=useState(false);
  const [layers,setLayers]=useState(()=>{try{const p=JSON.parse(localStorage.getItem("three-feet.layers")??"null");if(p&&[p.zone,p.base,p.objects].every(v=>typeof v==="boolean"))return p as {zone:boolean;base:boolean;objects:boolean};}catch{}return {zone:true,base:false,objects:true};});
  const [geometry,setGeometry]=useState<ViewGeometry|null>(null),[snapshot,setSnapshot]=useState(0);
  useEffect(()=>{try{localStorage.setItem("three-feet.layers",JSON.stringify(layers));}catch{}},[layers]);
  useEffect(()=>{let active=true;api.viewGeometry().then(g=>{if(active)setGeometry(g);}).catch(()=>{if(active)setGeometry(null);});return()=>{active=false;};},[calibrated,setupRevision]);
  useEffect(()=>{if(!failed)return;const id=setTimeout(()=>{setFailed(false);setAttempt(v=>v+1);},2000);return()=>clearTimeout(id);},[failed]);
  useEffect(()=>{if(!layers.base||!geometry?.configured||!cameraHealthy)return;const id=setInterval(()=>setSnapshot(v=>v+1),1500);return()=>clearInterval(id);},[layers.base,geometry?.configured,cameraHealthy]);
  const toggle=(key:keyof typeof layers)=>{setFailed(false);setLayers(p=>({...p,[key]:!p[key]}));};
  const query=new URLSearchParams({zone:String(layers.zone),base:String(layers.base),objects:String(layers.objects),a:String(attempt)});
  const corners=geometry?.base_corners_px,size=geometry?.image_size;
  let viewBox:string|undefined;
  if(corners?.length===4&&size&&corners.every(p=>p.length===2&&p.every(Number.isFinite))){const xs=corners.map(p=>p[0]),ys=corners.map(p=>p[1]);const extent=Math.max(Math.max(...xs)-Math.min(...xs),Math.max(...ys)-Math.min(...ys),35);const x=Math.max(0,Math.min(...xs)-extent*.65),y=Math.max(0,Math.min(...ys)-extent*.65);viewBox=`${x} ${y} ${Math.min(size[0]-x,extent*2.3)} ${Math.min(size[1]-y,extent*2.3)}`;}
  return <section className={`panel camera-panel ${className}`} aria-label="Camera and view controls">
    <header className="camera-toolbar"><div className="camera-heading"><h2>Live camera</h2><span>{simulated?"Simulated scene":"Your heater"}</span></div>
      <div className="view-controls" role="group" aria-label="Camera overlays">
        <button type="button" className="btn btn--sm" aria-pressed={layers.zone} onClick={()=>toggle("zone")}><Icon name="zone" size={16}/>Danger zone</button>
        <button type="button" className="btn btn--sm" aria-pressed={layers.base} onClick={()=>toggle("base")}><Icon name="base" size={16}/>Heater base</button>
        <button type="button" className="btn btn--sm" aria-pressed={layers.objects} onClick={()=>toggle("objects")}><Icon name="eye" size={16}/>Objects</button>
      </div>
    </header>
    <figure className="screen camera-stage m-0">
      {!failed&&<img key={query.toString()} src={`/stream.mjpg?${query}`} alt="Live heater camera with your selected overlays" width={1280} height={720} className={`h-full w-full object-contain ${cameraHealthy?"":"opacity-40"}`} onError={()=>setFailed(true)}/>}
      {failed&&<p className="p-8 text-paper-3">Video unavailable. Reconnecting…</p>}
      <span className="screen__tag absolute left-3 top-3"><span className="swatch" data-tone={cameraHealthy?"live":"info"}/>{cameraHealthy?"Live":"Camera offline"}{simulated?" / Simulated":""}</span>
      {layers.base&&viewBox&&corners&&size&&cameraHealthy&&<aside className="base-closeup" aria-label="Heater base close-up"><div>Base close-up</div><svg viewBox={viewBox} role="img" aria-label="Magnified floor footprint"><image href={`/api/snapshot.jpg?base=${snapshot}`} width={size[0]} height={size[1]}/><polygon points={corners.map(p=>p.join(",")).join(" ")} fill="none" stroke="var(--color-accent-2)" strokeWidth="2"/></svg><div>{geometry?.base_width_in} × {geometry?.base_depth_in} in</div></aside>}
      {calibrated&&<figcaption className="camera-legend screen__tag">{layers.zone&&<span><i className="legend-line bg-state-warn"/>Danger zone</span>}{layers.base&&<span><i className="legend-line bg-paper-3"/>Base footprint</span>}{!layers.zone&&!layers.base&&!layers.objects&&"Camera only"}</figcaption>}
    </figure>
    <div className="camera-caption"><div><strong>{calibrated?layers.base?"The heater’s floor footprint":"Choose what you see":"Map your heater’s footprint"}</strong><p>{calibrated?"View controls change the picture. Monitoring stays active.":"Enter base width and length to reveal the danger zone."}</p></div><button type="button" className={`btn btn--sm ${calibrated?"":"btn--primary"}`} onClick={onSetup}>{calibrated?"Adjust base":"Set up heater base"}</button></div>
  </section>;
}
