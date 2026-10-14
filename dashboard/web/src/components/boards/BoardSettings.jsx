import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { ErrorNote } from "../common.jsx";

/* A board's managers: name and key, item types, columns (states with a category and an optional WIP limit) and who
   may do what (view, ask, work on it, manage) per everyone / team / role / person. */

const TABS = [["general", "General"], ["types", "Item types"], ["states", "Columns"], ["sharing", "Who can do what"]];
export const LEVEL_LABEL = { view: "See it", request: "Ask the team (requests)", contribute: "Work on it", manage: "Manage it" };
const CAT_LABEL = { todo: "To do", doing: "In progress", done: "Done" };

export default function BoardSettings({ board, onClose, onChanged }) {
  const [tab, setTab] = useState("general");
  const [b, setB] = useState(board);
  const [gen, setGen] = useState({ name: board.name, key: board.key, description: board.description || "" });
  const [access, setAccess] = useState(board.access || []);
  const [newType, setNewType] = useState("");
  const [newState, setNewState] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);
  async function run(fn, msg = "Saved") {
    setBusy(true); setErr(""); setOk("");
    try {
      const out = await fn();
      const fresh = await api(`/boards/${board.id}`);
      setB(fresh); setAccess(fresh.access || []); setOk(msg); onChanged?.();
      return out ?? true;
    } catch (e) { setErr(e.message); return false; } finally { setBusy(false); }
  }
  const types = b.types || [];
  const states = b.states || [];
  const move = (list, i, by, what) => {
    const ids = list.map((x) => x.id);
    [ids[i], ids[i + by]] = [ids[i + by], ids[i]];
    run(() => api(`/boards/${b.id}/order/${what}`, { method: "PUT", body: { ids } }), "Order saved");
  };
  const who = (r) => (r.kind === "everyone" ? "everyone" : `${r.kind}:${r.subject}`);
  const setWho = (i, v) => {
    const [kind, subject = ""] = v.split(":");
    setAccess(access.map((r, j) => (j === i ? { ...r, kind, subject } : r)));
  };
  return (
    <div className="modal-backdrop" role="presentation" onClick={() => !busy && onClose()}>
      <div className="hud modal editor tabbed bd-settings" role="dialog" aria-modal="true" aria-label="Board settings" onClick={(e) => e.stopPropagation()}>
        <header className="hud-head"><h2><span className="hud-code">{b.key}</span>{b.name}</h2>
          <button type="button" className="btn ghost small" onClick={onClose} disabled={busy}>Close</button></header>
        <div className="seg roles-tabs" role="tablist">
          {TABS.map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{l}</button>)}
        </div>
        <ErrorNote error={err} />
        {ok && <div className="notice" role="status">{ok}</div>}

        {tab === "general" && (
          <form className="form-grid" onSubmit={(e) => { e.preventDefault(); run(() => api(`/boards/${b.id}`, { method: "PATCH", body: gen })); }}>
            <label className="field"><span>Name</span><input value={gen.name} maxLength={60} required onChange={(e) => setGen({ ...gen, name: e.target.value })} /></label>
            <label className="field"><span>Short key (item numbers like {gen.key || "KEY"}-12)</span>
              <input value={gen.key} maxLength={8} required onChange={(e) => setGen({ ...gen, key: e.target.value.toUpperCase() })} /></label>
            <label className="field wide"><span>What it's for</span><input value={gen.description} maxLength={300} onChange={(e) => setGen({ ...gen, description: e.target.value })} /></label>
            <div className="modal-actions wide">
              <button type="button" className="btn ghost small" disabled={busy}
                      onClick={() => run(() => api(`/boards/${b.id}`, { method: "PATCH", body: { archived: !b.archived } }), b.archived ? "Board restored" : "Board archived")}>
                {b.archived ? "Restore board" : "Archive board"}</button>
              <button className="btn primary small" disabled={busy}>Save</button>
            </div>
          </form>
        )}

        {tab === "types" && (
          <div className="bd-set-list">
            <p className="muted small">The kinds of work on this board. Removing a type keeps its items.</p>
            {types.map((t, i) => (
              <div key={t.id} className={`bd-set-row${t.archived ? " off" : ""}`}>
                <input type="color" value={t.color} aria-label="Colour" onChange={(e) => run(() => api(`/boards/${b.id}/types/${t.id}`, { method: "PATCH", body: { color: e.target.value } }))} />
                <input defaultValue={t.name} maxLength={30} aria-label="Name"
                       onBlur={(e) => e.target.value.trim() && e.target.value !== t.name && run(() => api(`/boards/${b.id}/types/${t.id}`, { method: "PATCH", body: { name: e.target.value } }))} />
                <button type="button" className="icon-x" disabled={i === 0} onClick={() => move(types, i, -1, "types")} aria-label="Up">↑</button>
                <button type="button" className="icon-x" disabled={i === types.length - 1} onClick={() => move(types, i, 1, "types")} aria-label="Down">↓</button>
                <button type="button" className="btn ghost small" onClick={() => run(() => api(`/boards/${b.id}/types/${t.id}`, { method: "PATCH", body: { archived: !t.archived } }))}>
                  {t.archived ? "Bring back" : "Remove"}</button>
              </div>
            ))}
            <form className="team-step-add" onSubmit={(e) => { e.preventDefault(); if (newType.trim()) run(() => api(`/boards/${b.id}/types`, { method: "POST", body: { name: newType } })).then(() => setNewType("")); }}>
              <input value={newType} maxLength={30} placeholder="New type, e.g. Design" onChange={(e) => setNewType(e.target.value)} />
              <button className="btn small" disabled={busy || !newType.trim()}>Add</button>
            </form>
          </div>
        )}

        {tab === "states" && (
          <div className="bd-set-list">
            <p className="muted small">Columns from left to right. Items in a <b>Done</b> column count as finished. A limit
              warns when a column holds too much work at once (0 = none). A column with items can't be removed.</p>
            {states.map((s, i) => (
              <div key={s.id} className={`bd-set-row${s.archived ? " off" : ""}`}>
                <input defaultValue={s.name} maxLength={30} aria-label="Name"
                       onBlur={(e) => e.target.value.trim() && e.target.value !== s.name && run(() => api(`/boards/${b.id}/states/${s.id}`, { method: "PATCH", body: { name: e.target.value } }))} />
                <select value={s.category} aria-label="Kind" onChange={(e) => run(() => api(`/boards/${b.id}/states/${s.id}`, { method: "PATCH", body: { category: e.target.value } }))}>
                  {Object.entries(CAT_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </select>
                <input type="number" min={0} max={999} defaultValue={s.wip_limit || 0} aria-label="Limit" title="Work-in-progress limit"
                       onBlur={(e) => Number(e.target.value) !== (s.wip_limit || 0) && run(() => api(`/boards/${b.id}/states/${s.id}`, { method: "PATCH", body: { wip_limit: Number(e.target.value) } }))} />
                <span className="meta">{s.items} item{s.items === 1 ? "" : "s"}</span>
                <button type="button" className="icon-x" disabled={i === 0} onClick={() => move(states, i, -1, "states")} aria-label="Left">↑</button>
                <button type="button" className="icon-x" disabled={i === states.length - 1} onClick={() => move(states, i, 1, "states")} aria-label="Right">↓</button>
                <button type="button" className="btn ghost small" onClick={() => run(() => api(`/boards/${b.id}/states/${s.id}`, { method: "PATCH", body: { archived: !s.archived } }))}>
                  {s.archived ? "Bring back" : "Remove"}</button>
              </div>
            ))}
            <form className="team-step-add" onSubmit={(e) => { e.preventDefault(); if (newState.trim()) run(() => api(`/boards/${b.id}/states`, { method: "POST", body: { name: newState, category: "doing" } })).then(() => setNewState("")); }}>
              <input value={newState} maxLength={30} placeholder="New column, e.g. Testing" onChange={(e) => setNewState(e.target.value)} />
              <button className="btn small" disabled={busy || !newState.trim()}>Add</button>
            </form>
          </div>
        )}

        {tab === "sharing" && (
          <div className="bd-set-list">
            <p className="muted small"><b>See it</b>: read only. <b>Ask the team</b>: may not add items but can send a task request to
              the team's manager (or the board's managers), who accepts or declines it. <b>Work on it</b>: add, change and move
              items. <b>Manage it</b>: this page too. A team's manager can always work on boards their team is on; whoever made
              the board manages it.</p>
            {access.map((r, i) => (
              <div key={i} className="bd-set-row">
                <select value={who(r)} aria-label="Who" onChange={(e) => setWho(i, e.target.value)}>
                  <option value="everyone">Everyone with boards</option>
                  <optgroup label="Teams">{(b.teams || []).map((t) => <option key={t.id} value={`group:${t.id}`}>{t.name}</option>)}</optgroup>
                  <optgroup label="Roles">{(b.roles || []).map((x) => <option key={x.name} value={`role:${x.name}`}>{x.label}</option>)}</optgroup>
                  <optgroup label="People">{(b.all_people || []).map((p) => <option key={p.id} value={`user:${p.id}`}>{p.display_name}</option>)}</optgroup>
                </select>
                <select value={r.level} aria-label="May" onChange={(e) => setAccess(access.map((x, j) => (j === i ? { ...x, level: e.target.value } : x)))}>
                  {Object.entries(LEVEL_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </select>
                <button type="button" className="icon-x" aria-label="Remove" onClick={() => setAccess(access.filter((_, j) => j !== i))}>×</button>
              </div>
            ))}
            <div className="modal-actions">
              <button type="button" className="btn ghost small" onClick={() => setAccess([...access, { kind: "group", subject: String(b.teams?.[0]?.id || ""), level: "contribute" }])}>+ Add</button>
              <button type="button" className="btn primary small" disabled={busy}
                      onClick={() => run(() => api(`/boards/${b.id}/access`, { method: "PUT", body: { rows: access.map(({ kind, subject, level }) => ({ kind, subject, level })) } }), "Sharing saved")}>Save</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
