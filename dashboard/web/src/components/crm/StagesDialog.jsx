import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { ErrorNote, Tag } from "../common.jsx";

/* crm.settings: the pipeline's stages: rename, probability for the forecast, order, add, remove (when empty). */

function StageRow({ s, first, last, busy, run }) {
  const [name, setName] = useState(s.name);
  const [prob, setProb] = useState(String(s.probability));
  const dirty = name.trim() !== s.name || (s.kind === "open" && Number(prob) !== s.probability);
  return (
    <li className={`crm-stage-row k-${s.kind}${s.archived ? " archived" : ""}`}>
      <input value={name} maxLength={40} aria-label="Stage name" disabled={busy || s.archived} onChange={(e) => setName(e.target.value)} />
      {s.kind === "open" ? (
        <label className="crm-stage-prob"><input type="number" min={0} max={100} value={prob} aria-label="Probability %"
                                                 disabled={busy || s.archived} onChange={(e) => setProb(e.target.value)} />%</label>
      ) : <Tag kind={s.kind === "won" ? "ok" : "bad"}>{s.kind} · {s.probability} %</Tag>}
      <span className="meta">{s.deals} {s.deals === 1 ? "deal" : "deals"}</span>
      <span className="crm-stage-actions">
        {dirty && <button className="btn primary small" disabled={busy}
                          onClick={() => run(`/crm-actions/stages/${s.id}`, "PATCH", { name: name.trim(), ...(s.kind === "open" ? { probability: Number(prob) } : {}) })}>Save</button>}
        {s.kind === "open" && !s.archived && (
          <>
            <button className="btn ghost small" disabled={busy || first} aria-label={`Move ${s.name} up`}
                    onClick={() => run(`/crm-actions/stages/${s.id}/move`, "POST", { direction: -1 })}>↑</button>
            <button className="btn ghost small" disabled={busy || last} aria-label={`Move ${s.name} down`}
                    onClick={() => run(`/crm-actions/stages/${s.id}/move`, "POST", { direction: 1 })}>↓</button>
          </>
        )}
        {s.kind === "open" && (
          <button className="btn ghost small" disabled={busy || (!s.archived && s.deals > 0)}
                  title={!s.archived && s.deals > 0 ? "Move its deals to another stage first" : ""}
                  onClick={() => run(`/crm-actions/stages/${s.id}/archive`, "POST", { archived: !s.archived })}>{s.archived ? "Restore" : "Remove"}</button>
        )}
      </span>
    </li>
  );
}

export default function StagesDialog({ onClose, onChanged }) {
  const [list, setList] = useState(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => { api("/crm-actions/stages").then(setList).catch((e) => setErr(e.message)); }, []);
  async function run(path, method, body) {
    setBusy(true); setErr("");
    try { setList(await api(path, { method, body })); onChanged?.(); return true; }
    catch (e) { setErr(e.message); return false; } finally { setBusy(false); }
  }
  const open = (list || []).filter((s) => s.kind === "open" && !s.archived);
  return (
    <div className="modal-backdrop" role="presentation" onClick={() => !busy && onClose()}>
      <div className="hud modal editor" role="dialog" aria-modal="true" aria-label="Pipeline stages" onClick={(e) => e.stopPropagation()}>
        <header className="hud-head"><h2>Pipeline stages</h2>
          <button className="btn ghost small" onClick={onClose} disabled={busy}>Close</button></header>
        <p className="muted small">The open stages in order, each with the chance of winning a deal there (used for the weighted
          forecast; a single deal can override it). Won and Lost always come last.</p>
        <ErrorNote error={err} />
        <ol className="crm-stage-list">
          {(list || []).map((s) => (
            <StageRow key={`${s.id}:${s.name}:${s.probability}:${s.archived}`} s={s} busy={busy} run={run}
                      first={open[0]?.id === s.id} last={open[open.length - 1]?.id === s.id} />
          ))}
        </ol>
        <form className="field-row" onSubmit={async (e) => { e.preventDefault(); if (await run("/crm-actions/stages", "POST", { name: name.trim() })) setName(""); }}>
          <label className="field"><span>New stage</span>
            <input value={name} maxLength={40} placeholder="e.g. Demo booked" onChange={(e) => setName(e.target.value)} />
          </label>
          <button className="btn primary small" type="submit" disabled={busy || !name.trim()}>Add</button>
        </form>
      </div>
    </div>
  );
}
