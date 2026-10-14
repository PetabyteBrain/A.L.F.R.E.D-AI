import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { ErrorNote } from "../common.jsx";

/* A new board from a template, shared right away: with everyone, with chosen teams (everyone else may ask), or
   only with oneself. Everything can be changed later in the board's settings. */

export default function NewBoard({ templates, onCreated, onClose }) {
  const [teams, setTeams] = useState([]);
  useEffect(() => { api("/teams").then((d) => setTeams(d.teams)).catch(() => {}); }, []);
  const [f, setF] = useState({ name: "", key: "", description: "", template: "agile" });
  const [share, setShare] = useState("everyone");
  const [picked, setPicked] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);
  async function save(e) {
    e.preventDefault();
    setBusy(true); setErr("");
    const sharing = share === "everyone" ? [{ kind: "everyone", level: "contribute" }]
      : share === "teams" ? [...picked.map((id) => ({ kind: "group", subject: String(id), level: "contribute" })), { kind: "everyone", level: "request" }]
        : [];
    try { onCreated(await api("/boards", { method: "POST", body: { ...f, sharing } })); }
    catch (e2) { setErr(e2.message); } finally { setBusy(false); }
  }
  return (
    <div className="modal-backdrop" role="presentation" onClick={() => !busy && onClose()}>
      <form className="hud modal editor" role="dialog" aria-modal="true" aria-label="New board" onClick={(e) => e.stopPropagation()} onSubmit={save}>
        <header className="hud-head"><h2><span className="hud-code">BD</span>New board</h2>
          <button type="button" className="btn ghost small" onClick={onClose} disabled={busy}>Close</button></header>
        <ErrorNote error={err} />
        <div className="form-grid">
          <label className="field"><span>Name</span><input autoFocus required value={f.name} maxLength={60} placeholder="e.g. Website relaunch" onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
          <label className="field"><span>Short key (optional)</span><input value={f.key} maxLength={8} placeholder="e.g. WEB" onChange={(e) => setF({ ...f, key: e.target.value.toUpperCase() })} /></label>
          <label className="field wide"><span>What it's for (optional)</span><input value={f.description} maxLength={300} onChange={(e) => setF({ ...f, description: e.target.value })} /></label>
        </div>
        <div className="field"><span>Start from</span>
          <div className="bd-choices">
            {Object.entries(templates || {}).map(([k, l]) => (
              <label key={k} className={`bd-choice${f.template === k ? " on" : ""}`}><input type="radio" name="tpl" checked={f.template === k} onChange={() => setF({ ...f, template: k })} />{l}</label>
            ))}
          </div>
          <span className="muted tiny">Item types and columns can be changed any time.</span>
        </div>
        <div className="field"><span>Who can work on it</span>
          <div className="bd-choices">
            <label className={`bd-choice${share === "everyone" ? " on" : ""}`}><input type="radio" name="share" checked={share === "everyone"} onChange={() => setShare("everyone")} />Everyone with boards</label>
            <label className={`bd-choice${share === "teams" ? " on" : ""}`}><input type="radio" name="share" checked={share === "teams"} onChange={() => setShare("teams")} />Chosen teams (the others can send requests)</label>
            <label className={`bd-choice${share === "me" ? " on" : ""}`}><input type="radio" name="share" checked={share === "me"} onChange={() => setShare("me")} />Only me for now</label>
          </div>
          {share === "teams" && (
            <div className="pick-chips">
              {(teams || []).map((t) => (
                <button key={t.id} type="button" className={`chip${picked.includes(t.id) ? "" : " off"}`} aria-pressed={picked.includes(t.id)}
                        onClick={() => setPicked(picked.includes(t.id) ? picked.filter((x) => x !== t.id) : [...picked, t.id])}>{t.name}</button>
              ))}
              {(teams || []).length === 0 && <span className="muted small">No teams yet: create them on the Team tab.</span>}
            </div>
          )}
        </div>
        <div className="modal-actions">
          <button type="button" className="btn ghost small" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn primary small" disabled={busy || !f.name.trim() || (share === "teams" && !picked.length)}>Create board</button>
        </div>
      </form>
    </div>
  );
}
