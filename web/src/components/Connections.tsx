import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "../lib/api";
import type { Connections as ConnectionsInfo, CyncDevice } from "../types";
import { Icon } from "./Icon";
import "../setup.css";
import "../connections.css";

type Note = { tone: "ok" | "error"; text: string } | null;

function Message({ note }: { note: Note }) {
  if (!note) return null;
  return (
    <p className={note.tone === "error" ? "setup-error" : "conn-ok"} role={note.tone === "error" ? "alert" : "status"}>
      {note.text}
    </p>
  );
}

/** Choose the camera and the plug from the dashboard. Changes apply live; a heater that is on only gets a warning. */
export function Connections({ open, onClose, heaterOn }: { open: boolean; onClose: () => void; heaterOn: boolean | null }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [info, setInfo] = useState<ConnectionsInfo | null>(null);
  const [loadError, setLoadError] = useState("");

  const [address, setAddress] = useState("");
  const [user, setUser] = useState("");
  const [password, setPassword] = useState("");
  const [verifyTls, setVerifyTls] = useState(false);
  const [cameraBusy, setCameraBusy] = useState<"test" | "apply" | null>(null);
  const [cameraNote, setCameraNote] = useState<Note>(null);

  const [kind, setKind] = useState("cync");
  const [plugName, setPlugName] = useState("");
  const [devices, setDevices] = useState<CyncDevice[]>([]);
  const [plugBusy, setPlugBusy] = useState(false);
  const [plugNote, setPlugNote] = useState<Note>(null);
  const [email, setEmail] = useState("");
  const [cyncPassword, setCyncPassword] = useState("");
  const [code, setCode] = useState("");
  const [needsCode, setNeedsCode] = useState(false);
  const [loginBusy, setLoginBusy] = useState(false);
  const [loginNote, setLoginNote] = useState<Note>(null);
  const [reconnect, setReconnect] = useState(false);

  const fill = (c: ConnectionsInfo) => {
    setInfo(c);
    setAddress(c.camera.address);
    setUser(c.camera.user);
    setVerifyTls(c.camera.verify_tls);
    setPassword("");
    setKind(c.plug.kind === "dryrun" ? "dryrun" : "cync");
    setPlugName(c.plug.name);
    setDevices(c.plug.devices);
  };

  useEffect(() => {
    if (!open) {
      dialog.current?.close();
      return;
    }
    if (!dialog.current?.open) dialog.current?.showModal();
    setLoadError("");
    setCameraNote(null);
    setPlugNote(null);
    setLoginNote(null);
    setNeedsCode(false);
    setReconnect(false);
    let active = true;
    api
      .connections()
      .then((c) => active && fill(c))
      .catch((e) => active && setLoadError((e as Error).message));
    return () => {
      active = false;
    };
  }, [open]);

  const editable = info?.editable ?? false;
  const heaterWarning = heaterOn !== false;
  const heaterText = heaterOn === true ? "The heater is on right now." : "The heater's state is unknown right now.";

  const cameraInput = () => ({ address: address.trim(), user: user.trim(), password: password || undefined, verify_tls: verifyTls });

  const testCamera = async () => {
    setCameraBusy("test");
    setCameraNote(null);
    try {
      const r = await api.testCamera(cameraInput());
      setCameraNote({ tone: "ok", text: `A picture arrived (${r.width} × ${r.height}). This camera works.` });
    } catch (e) {
      setCameraNote({ tone: "error", text: (e as Error).message });
    } finally {
      setCameraBusy(null);
    }
  };

  const applyCamera = async () => {
    setCameraBusy("apply");
    setCameraNote(null);
    try {
      fill(await api.setCamera(cameraInput()));
      setCameraNote({
        tone: "ok",
        text: "Camera changed. Check the live view: the danger zone must still sit on the heater base. If the view moved, redo Heater setup.",
      });
    } catch (e) {
      setCameraNote({ tone: "error", text: (e as Error).message });
    } finally {
      setCameraBusy(null);
    }
  };

  const signIn = async () => {
    setLoginBusy(true);
    setLoginNote(null);
    try {
      const r = await api.cyncLogin({ email: email.trim(), password: cyncPassword });
      setCyncPassword("");
      if (r.status === "code_required") {
        setNeedsCode(true);
        setLoginNote({ tone: "ok", text: "Cync emailed you a code. Enter it below." });
      } else {
        await signedIn();
      }
    } catch (e) {
      setLoginNote({ tone: "error", text: (e as Error).message });
    } finally {
      setLoginBusy(false);
    }
  };

  const sendCode = async () => {
    setLoginBusy(true);
    setLoginNote(null);
    try {
      await api.cyncCode(code.trim());
      setCode("");
      setNeedsCode(false);
      await signedIn();
    } catch (e) {
      setLoginNote({ tone: "error", text: (e as Error).message });
    } finally {
      setLoginBusy(false);
    }
  };

  const signedIn = async () => {
    setReconnect(true);
    setLoginNote({ tone: "ok", text: "Signed in to Cync. Choose the plug, then use it." });
    const c = await api.connections();
    setInfo(c);
  };

  const applyPlug = async () => {
    setPlugBusy(true);
    setPlugNote(null);
    try {
      const c = await api.setPlug({ kind, name: kind === "cync" ? plugName.trim() : "" });
      fill(c);
      setReconnect(false);
      setPlugNote({ tone: "ok", text: "Plug changed. Make sure it really controls your heater before you rely on it." });
    } catch (e) {
      if (e instanceof ApiError && e.devices.length > 0) setDevices(e.devices);
      setPlugNote({ tone: "error", text: (e as Error).message });
    } finally {
      setPlugBusy(false);
    }
  };

  const signedInToCync = info?.plug.signed_in ?? false;
  const cyncReady = signedInToCync || reconnect;
  const plugChanged = info ? kind !== info.plug.kind || plugName.trim() !== info.plug.name || reconnect : false;

  // Shows the sign-in form again without touching the saved login until a new one succeeds.
  const useAnotherAccount = () => {
    setReconnect(false);
    setNeedsCode(false);
    setLoginNote(null);
    if (info) setInfo({ ...info, plug: { ...info.plug, signed_in: false } });
  };

  return (
    <dialog
      ref={dialog}
      className="heater-dialog"
      onCancel={onClose}
      onClose={onClose}
      onPointerDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      aria-labelledby="connections-title"
    >
      <header className="setup-header">
        <div>
          <h2 id="connections-title">Camera and plug</h2>
          <p>Change what Three Feet watches and switches. It applies right away and is remembered after a restart.</p>
        </div>
        <button type="button" className="btn btn--icon" onClick={onClose} aria-label="Close camera and plug settings">
          <Icon name="x" />
        </button>
      </header>

      {loadError && <p className="setup-error conn-pad" role="alert">{loadError}</p>}
      {info && !editable && (
        <p className="setup-feedback conn-pad-x" role="status">
          This is the simulated scene, so there is no real camera or plug to change. Start the server with
          <code> --source phone --plug cync</code> to connect real ones.
        </p>
      )}

      <div className="conn-layout">
        <section aria-labelledby="camera-title" className="conn-section">
          <span className="setup-step">Camera</span>
          <h3 id="camera-title">Where the picture comes from</h3>
          <p className="setup-help">
            Now: {info ? (info.camera.address ? `${info.camera.address} (${info.camera.healthy ? "sending pictures" : "no picture"})` : "no camera yet") : "loading"}
          </p>
          <label className="setup-field">
            Camera address
            <span>A stream address, like https://192.168.0.63:4444/video/mjpeg, or a webcam number such as 0</span>
            <input
              type="text"
              inputMode="url"
              autoComplete="off"
              spellCheck={false}
              value={address}
              disabled={!editable}
              onChange={(e) => setAddress(e.target.value)}
            />
          </label>
          <label className="setup-field">
            Username <span>Only if the camera app asks for one</span>
            <input type="text" autoComplete="off" value={user} disabled={!editable} onChange={(e) => setUser(e.target.value)} />
          </label>
          {user.trim() && (
            <label className="setup-field">
              Password <span>{info?.camera.has_password ? "Leave blank to keep the current password" : "Saved on this computer only"}</span>
              <input
                type="password"
                autoComplete="new-password"
                value={password}
                disabled={!editable}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>
          )}
          <label className="conn-check">
            <input type="checkbox" checked={verifyTls} disabled={!editable} onChange={(e) => setVerifyTls(e.target.checked)} />
            Verify the camera's HTTPS certificate
            <span>Leave off for a phone camera app, which uses a self-signed certificate.</span>
          </label>
          {heaterWarning && editable && (
            <p className="conn-warning" role="note">
              {heaterText} Changing the camera clears what Three Feet has detected and its empty-zone photo, so check the
              danger zone still sits on the heater.
            </p>
          )}
          <div className="conn-actions">
            <button type="button" className="btn" disabled={!editable || !address.trim() || cameraBusy !== null} aria-busy={cameraBusy === "test"} onClick={testCamera}>
              {cameraBusy === "test" ? "Testing…" : "Test camera"}
            </button>
            <button type="button" className="btn btn--primary" disabled={!editable || !address.trim() || cameraBusy !== null} aria-busy={cameraBusy === "apply"} onClick={applyCamera}>
              {cameraBusy === "apply" ? "Connecting…" : heaterWarning ? "Use this camera anyway" : "Use this camera"}
            </button>
          </div>
          <Message note={cameraNote} />
        </section>

        <section aria-labelledby="plug-title" className="conn-section">
          <span className="setup-step">Plug</span>
          <h3 id="plug-title">What switches the heater</h3>
          <p className="setup-help">
            Now: {info ? `${info.plug.kind === "cync" ? "GE Cync plug" : "test plug"}${info.plug.name ? ` “${info.plug.name}”` : ""} (${info.plug.connected ? "connected" : "not reachable"})` : "loading"}
          </p>
          <label className="setup-field">
            Plug type
            <select value={kind} disabled={!editable} onChange={(e) => setKind(e.target.value)}>
              <option value="cync">GE Cync smart plug</option>
              <option value="dryrun">Test plug (simulated, switches nothing)</option>
            </select>
          </label>

          {kind === "dryrun" && (
            <p className="conn-warning" role="note">
              The test plug only pretends to cut power. It will not turn off a real heater.
            </p>
          )}

          {kind === "cync" && (
            <>
              <div className="conn-signin">
                <strong>{cyncReady ? "Signed in to Cync" : "Sign in to Cync"}</strong>
                {cyncReady && !needsCode && (
                  <button type="button" className="btn btn--sm" disabled={!editable} onClick={useAnotherAccount}>
                    Use another account
                  </button>
                )}
                {!cyncReady && !needsCode && (
                  <>
                    <label className="setup-field">
                      Cync email
                      <input type="email" autoComplete="username" value={email} disabled={!editable || loginBusy} onChange={(e) => setEmail(e.target.value)} />
                    </label>
                    <label className="setup-field">
                      Cync password <span>Used once to sign in. It is never saved.</span>
                      <input type="password" autoComplete="current-password" value={cyncPassword} disabled={!editable || loginBusy} onChange={(e) => setCyncPassword(e.target.value)} />
                    </label>
                    <button type="button" className="btn" disabled={!editable || loginBusy || !email.trim() || !cyncPassword} aria-busy={loginBusy} onClick={signIn}>
                      {loginBusy ? "Signing in…" : "Sign in"}
                    </button>
                  </>
                )}
                {needsCode && (
                  <>
                    <label className="setup-field">
                      Code from Cync <span>Check your email</span>
                      <input type="text" inputMode="numeric" autoComplete="one-time-code" value={code} disabled={loginBusy} onChange={(e) => setCode(e.target.value)} />
                    </label>
                    <button type="button" className="btn" disabled={loginBusy || !code.trim()} aria-busy={loginBusy} onClick={sendCode}>
                      {loginBusy ? "Checking…" : "Confirm code"}
                    </button>
                  </>
                )}
                <Message note={loginNote} />
              </div>

              <label className="setup-field">
                Plug name <span>As it appears in the Cync app. Blank uses the only plug on the account.</span>
                <input type="text" list="cync-devices" autoComplete="off" value={plugName} disabled={!editable} onChange={(e) => setPlugName(e.target.value)} />
                <datalist id="cync-devices">
                  {devices.map((d) => (
                    <option key={d.name} value={d.name}>
                      {d.type}
                      {d.online ? "" : " (offline)"}
                    </option>
                  ))}
                </datalist>
              </label>
              {devices.length > 0 && (
                <p className="setup-help">On this account: {devices.map((d) => d.name).join(", ")}</p>
              )}
            </>
          )}

          {heaterWarning && editable && plugChanged && (
            <p className="conn-warning" role="note">
              {heaterText} Until the new plug is in use nothing can switch the heater off, and the old plug stops being
              watched. Switch the heater by hand if you need it off first.
            </p>
          )}
          <div className="conn-actions">
            <button
              type="button"
              className="btn btn--primary"
              disabled={!editable || plugBusy || (kind === "cync" && !cyncReady) || !plugChanged}
              aria-busy={plugBusy}
              onClick={applyPlug}
            >
              {plugBusy ? "Connecting…" : heaterWarning && plugChanged ? "Use this plug anyway" : "Use this plug"}
            </button>
          </div>
          <p className="setup-help">Wait for the current power command to finish before changing plugs.</p>
          <Message note={plugNote} />
        </section>
      </div>
    </dialog>
  );
}
