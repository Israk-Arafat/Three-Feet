import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import type { SetupState } from "../types";
import { Icon } from "./Icon";
import "../setup.css";

const CORNERS = ["Front-left", "Front-right", "Back-right", "Back-left"];
export function HeaterSetup({open,onClose,onSaved}:{open:boolean;onClose:()=>void;onSaved?:()=>void}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const notified = useRef<string|null>(null);
  const [setup,setSetup] = useState<SetupState|null>(null);
  const [width,setWidth] = useState("4.5"), [depth,setDepth] = useState("4.35");
  const [busy,setBusy] = useState(false), [error,setError] = useState("");
  const [manual,setManual] = useState(false), [overlay,setOverlay] = useState(true);
  const [points,setPoints] = useState<number[][]>([]);
  const [zoom,setZoom] = useState(1);
  useEffect(()=>{
    if (!open) { dialog.current?.close(); return; }
    if (!dialog.current?.open) dialog.current?.showModal();
    setManual(false);setPoints([]);setZoom(1);setError("");
    let active=true;
    api.setup().then(s=>{if(active){setSetup(s);setWidth(String(s.width_in));setDepth(String(s.depth_in));}}).catch(e=>{if(active)setError(e.message);});
    return ()=>{active=false;};
  },[open]);
  useEffect(()=>{
    if(!open || setup?.status!=="running") return;
    let active=true;
    const id=window.setInterval(()=>api.setup().then(s=>{if(active)setSetup(s);}).catch(e=>{if(active)setError(e.message);}),1200);
    return ()=>{active=false;window.clearInterval(id);};
  },[open,setup?.status]);
  useEffect(()=>{
    if(setup?.status==="ready" && setup.job_id && notified.current!==setup.job_id){notified.current=setup.job_id;onSaved?.();}
  },[setup,onSaved]);
  const running=busy||setup?.status==="running";
  const dimensions=()=>{
    const w=Number(width), d=Number(depth);
    if(!Number.isFinite(w)||!Number.isFinite(d)||w<=0||d<=0||w>120||d>120)throw new Error("Enter a width and length between 0 and 120 inches.");
    return {width_in:w,depth_in:d};
  };
  const act=async(action:"auto"|"capture"|"save")=>{
    setBusy(true);setError("");
    try{
      const body=dimensions();
      let s:SetupState;
      if(action==="auto"){setManual(false);setPoints([]);setZoom(1);s=await api.findHeaterBase(body);}
      else if(action==="capture"){s=await api.captureSetup(body);setManual(true);setPoints([]);setZoom(1);}
      else {if(!setup?.job_id||points.length!==4)throw new Error("Mark all four base corners first.");s=await api.saveManualSetup({...body,job_id:setup.job_id,corners_px:points});}
      setSetup(s);
    }catch(e){setError((e as Error).message);}finally{setBusy(false);}
  };
  const imageUrl=manual||!overlay?setup?.image_url:setup?.overlay_url||setup?.image_url;
  return <dialog ref={dialog} className="heater-dialog" onCancel={onClose} onClose={onClose} onPointerDown={e=>{if(e.target===e.currentTarget)onClose();}} aria-labelledby="heater-setup-title">
    <header className="setup-header"><div><h2 id="heater-setup-title">Set up your heater</h2><p>Two measurements. One clear view of its footprint.</p></div><button type="button" className="btn btn--icon" onClick={onClose} aria-label="Close heater setup"><Icon name="x"/></button></header>
    <div className="setup-layout">
      <section className="setup-measurements" aria-labelledby="base-size-title">
        <span className="setup-step">1. Measure the base</span><h3 id="base-size-title">Where it meets the floor</h3>
        <svg viewBox="0 0 250 150" className="base-diagram" role="img" aria-label="Top view: width runs left to right, length runs front to back">
          <path d="M65 45h120v65H65z" fill="var(--color-paper-2)" stroke="currentColor" strokeWidth="2"/>
          <path d="M65 27h120M65 22v10M185 22v10M205 45v65M200 45h10M200 110h10" fill="none" stroke="var(--color-accent)" strokeWidth="2"/>
          <text x="125" y="17" textAnchor="middle">Width</text><text x="218" y="83" textAnchor="middle" transform="rotate(90 218 83)">Length</text><text x="125" y="135" textAnchor="middle">Front of heater</text>
        </svg>
        <label className="setup-field">Base width <span>Left to right, in inches</span><input type="number" inputMode="decimal" min="0.01" max="120" step="0.01" value={width} disabled={running} onChange={e=>setWidth(e.target.value)}/></label>
        <label className="setup-field">Base length <span>Front to back, in inches</span><input type="number" inputMode="decimal" min="0.01" max="120" step="0.01" value={depth} disabled={running} onChange={e=>setDepth(e.target.value)}/></label>
        <p className="setup-help">Measure the footprint, not the height. Keep the phone fixed after setup.</p>
        <button type="button" className="btn btn--primary setup-wide" disabled={running} onClick={()=>act("auto")} aria-busy={running}>{running?"Finding the base…":"Find heater base"}</button>
        <p className="setup-source">Muse Spark selects the floor-contact corners.</p>
        <button type="button" className="btn setup-wide" disabled={running} onClick={()=>act("capture")}>Mark base corners myself</button>
      </section>
      <section className="setup-preview" aria-labelledby="base-review-title">
        <span className="setup-step">2. Review the footprint</span><h3 id="base-review-title">Check the heater base</h3>
        <div className="setup-feedback" role="status" data-status={setup?.status??"idle"}>
          {running?"Looking at the camera image and checking the base corners. This can take a minute.":setup?.status==="needs_review"&&!manual?"The base needs review. Inspect the selection below, try a clearer view, or mark its corners yourself.":setup?.message??"Add your measurements, then let Meta find the heater base."}
          {setup?.status==="needs_review"&&!manual&&setup.quality&&<details><summary>Why does it need review?</summary><p>{setup.message}</p></details>}
        </div>
        {error && <p className="setup-error" role="alert">{error}</p>}
        {imageUrl && setup?.image_size ? <>
          {!manual&&setup.overlay_url&&<button type="button" className="btn btn--sm" aria-pressed={overlay} onClick={()=>setOverlay(!overlay)}>{overlay?"Show original photo":"Show base selection"}</button>}
          {!manual&&setup.status==="needs_review"&&setup.corners_px?.length===4&&<button type="button" className="btn btn--sm" onClick={()=>{setPoints(setup.corners_px!.map(p=>[...p]));setManual(true);setError("");}}>Use and edit these corners</button>}
          <div className="setup-zoom"><span>Photo zoom {zoom}×</span><button type="button" className="btn btn--sm" disabled={zoom===1} onClick={()=>setZoom(z=>Math.max(1,z-1))}>Zoom out</button><button type="button" className="btn btn--sm" disabled={zoom===4} onClick={()=>setZoom(z=>Math.min(4,z+1))}>Zoom in</button></div>
          <div className="setup-image-viewport"><div className={`setup-image ${manual?"setup-image--mark":""}`} style={{width:Math.min(520,440*setup.image_size[0]/setup.image_size[1])*zoom,maxWidth:zoom===1?"100%":"none"}}>
            <img src={imageUrl} alt={manual?"Frozen camera image. Mark the four floor-contact corners.":"Heater base selection for review"} draggable={false} onClick={e=>{
              if(!manual||points.length>=4||running||!setup.image_size)return;
              const r=e.currentTarget.getBoundingClientRect();setPoints(p=>[...p,[(e.clientX-r.left)/r.width*setup.image_size![0],(e.clientY-r.top)/r.height*setup.image_size![1]]]);
            }}/>
            {manual&&<svg viewBox={`0 0 ${setup.image_size[0]} ${setup.image_size[1]}`} className="setup-points" aria-hidden="true">
              <polyline points={points.map(p=>p.join(",")).join(" ")} fill="none" stroke="var(--color-accent-2)" strokeWidth="3"/>
              {points.map((p,i)=><g key={i}><circle cx={p[0]} cy={p[1]} r="7" fill="var(--color-accent-2)" stroke="var(--color-ink)" strokeWidth="2"/><text x={p[0]+12} y={p[1]-10} fill="var(--color-paper-3)" stroke="var(--color-ink)" strokeWidth="3" paintOrder="stroke" fontSize="22">{i+1}</text></g>)}
            </svg>}
          </div></div>
          {manual&&<div className="manual-controls"><p>{points.length<4?`Click ${CORNERS[points.length]} (${points.length+1} of 4).` : "All four corners marked. Check the outline, then save."} Face the heater when choosing left and right.</p><div><button type="button" className="btn btn--sm" disabled={!points.length||running} onClick={()=>setPoints(p=>p.slice(0,-1))}>Undo point</button><button type="button" className="btn btn--primary btn--sm" disabled={points.length!==4||running} onClick={()=>act("save")}>Save calibration</button></div></div>}
        </>:<div className="setup-placeholder"><Icon name="base" size={48}/>{setup?.calibrated
          ?<><p>Your heater base is already set up.</p><span>To check it against a fresh camera image, find the base again or mark its corners yourself.</span></>
          :<><p>Your heater base will appear here.</p><span>A higher, diagonal camera view makes the floor footprint easier to see.</span></>}</div>}
        {setup?.status==="ready"&&<div className="setup-done"><Icon name="check"/><p>Ready. The danger zone follows this base.</p><button type="button" className="btn btn--primary" onClick={onClose}>Back to live view</button></div>}
      </section>
    </div>
  </dialog>;
}
