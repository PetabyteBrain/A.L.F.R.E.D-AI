import { useState } from "react";
import { api } from "../../api.js";
import { fmtDate } from "../../format.js";
import { usePoll } from "../../hooks.js";
import { Empty, ErrorNote } from "../common.jsx";
import { Icon } from "../icons.jsx";

/* Onboarding and offboarding checklists: each step has a due date and a person responsible, who is reminded on the
   morning it's due. Whoever is responsible, or anyone managing employees, ticks steps off. */

export function ChecklistCard({ list, status, canManage, onChanged, onOpen }) {
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");
  const [busy, setBusy] = useState(0);
  const [err, setErr] = useState("");
  const pct = list.total ? Math.round((list.done / list.total) * 100) : 0;
  async function run(id, fn) {
    setBusy(id); setErr("");
    try { await fn(); onChanged?.(); } catch (e) { setErr(e.message); } finally { setBusy(0); }
  }
  const tick = (t) => run(t.id, () => api(`/team/tasks/${t.id}`, { method: "PATCH", body: { done: !t.done_at } }));
  const assign = (t, uid) => run(t.id, () => api(`/team/tasks/${t.id}`, { method: "PATCH", body: { assignee_id: Number(uid) || 0 } }));
  const remove = (t) => run(t.id, () => api(`/team/tasks/${t.id}`, { method: "DELETE" }));
  async function add(e) {
    e.preventDefault();
    if (!title.trim()) return;
    await run(-1, () => api(`/team/checklists/${list.id}/tasks`, { method: "POST", body: { title, due_date: due || null } }));
    setTitle(""); setDue("");
  }
  const kind = list.kind === "onboarding" ? "Onboarding" : "Offboarding";
  return (
    <section className={`team-card team-checklist k-${list.kind}${list.closed_at ? " closed" : ""}`}>
      <h3 className="sub"><Icon name="tasks" className="lab-ico" />
        {onOpen ? <button type="button" className="linkish" onClick={() => onOpen(list.employee_id)}>{list.employee_name}</button> : null}
        {onOpen ? `: ${kind.toLowerCase()}` : kind}
        <span className="mail-count plain">{list.done}/{list.total}</span>
        {list.anchor_date && <span className="meta">{list.kind === "onboarding" ? "first day" : "last day"} {fmtDate(list.anchor_date)}</span>}
      </h3>
      <div className="team-progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${pct}%` }} /></div>
      <ErrorNote error={err} />
      <ul className="team-steps">
        {list.tasks.map((t) => (
          <li key={t.id} className={`${t.done_at ? "done" : ""}${t.overdue ? " overdue" : ""}${t.mine ? " mine" : ""}`}>
            <label className="team-step-check">
              <input type="checkbox" checked={!!t.done_at} disabled={busy === t.id || !(canManage || t.mine)} onChange={() => tick(t)} />
              <span className="team-step-title">{t.title}</span>
            </label>
            <span className="meta team-step-due">{t.due_date ? (t.overdue ? `overdue, ${fmtDate(t.due_date)}` : fmtDate(t.due_date)) : ""}</span>
            {canManage && status?.people?.length ? (
              <select className="crm-sort team-step-who" value={t.assignee_id || ""} disabled={busy === t.id} aria-label="Responsible"
                      onChange={(e) => assign(t, e.target.value)}>
                <option value="">Nobody</option>
                {status.people.map((p) => <option key={p.id} value={p.id}>{p.display_name}</option>)}
              </select>
            ) : <span className="meta team-step-who">{t.done_at ? `done by ${t.done_by_name || "someone"}` : t.assignee_name || ""}</span>}
            {canManage && <button type="button" className="icon-x" aria-label={`Remove ${t.title}`} disabled={busy === t.id} onClick={() => remove(t)}>×</button>}
          </li>
        ))}
      </ul>
      {canManage && !list.closed_at && (
        <form className="team-step-add" onSubmit={add}>
          <input value={title} maxLength={160} onChange={(e) => setTitle(e.target.value)} placeholder="Add a step…" aria-label="New step" />
          <input type="date" value={due} onChange={(e) => setDue(e.target.value)} aria-label="Due" />
          <button className="btn small" disabled={!title.trim() || busy === -1}>Add</button>
          <button type="button" className="btn ghost small" disabled={busy === -2}
                  onClick={() => run(-2, () => api(`/team/checklists/${list.id}/close`, { method: "POST" }))}>Close checklist</button>
        </form>
      )}
    </section>
  );
}

export default function Checklists({ status, onOpen, onChanged }) {
  const [mine, setMine] = useState(!status?.can?.view);
  const [closed, setClosed] = useState(false);
  const { data, error, reload } = usePoll(() => api(`/team/checklists?mine=${mine}&closed=${closed}`), 60000, [mine, closed]);
  const lists = data || [];
  return (
    <div className="team-checklists">
      <div className="mail-toolbar">
        <div className="mail-filters">
          <b className="mail-title">Checklists</b>
          {status?.can?.view && (
            <div className="seg" role="tablist" aria-label="Whose">
              {[[false, "All"], [true, "My steps"]].map(([v, l]) => (
                <button key={l} role="tab" aria-selected={mine === v} className={mine === v ? "active" : ""} onClick={() => setMine(v)}>{l}</button>
              ))}
            </div>
          )}
          <label className="mail-unread-toggle"><input type="checkbox" checked={closed} onChange={(e) => setClosed(e.target.checked)} /> Include completed</label>
        </div>
      </div>
      <ErrorNote error={error} />
      {data && lists.length === 0 && (
        <Empty>{mine ? "No onboarding or offboarding steps for you." : "No onboarding or offboarding running. Start one from an employee (More ▾), or it starts on approval of a new employee."}</Empty>
      )}
      {lists.map((l) => <ChecklistCard key={l.id} list={l} status={status} canManage={!!status?.can?.manage} onOpen={onOpen}
                                       onChanged={() => { reload(); onChanged?.(); }} />)}
    </div>
  );
}
