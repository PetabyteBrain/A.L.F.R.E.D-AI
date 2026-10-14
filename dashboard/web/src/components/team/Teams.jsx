import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { usePoll } from "../../hooks.js";
import { Empty, ErrorNote, Tag } from "../common.jsx";
import { Icon } from "../icons.jsx";
import { initials } from "../boards/BoardView.jsx";

/* Teams (roadmap #59): the people groups, with a manager (who decides task requests for the team) and a colour (the
   team's lane on the boards). Groups that give roles (permissions) are changed under Settings → Roles. */

const COLORS = ["#5ad0e6", "#b58cff", "#7aa7ff", "#ffb040", "#5affc4", "#ff6b6b", "#e6d75a", "#ff8ad8"];

function TeamEditor({ team, people, canRoles, onClose, onSaved }) {
  const isNew = !team;
  const [f, setF] = useState({ name: team?.name || "", description: team?.description || "", color: team?.color || COLORS[0],
                               manager_id: team?.manager_id || "" });
  const [members, setMembers] = useState(() => new Set((team?.members || []).map((m) => m.id)));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const locked = team?.gives_roles && !canRoles;
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);
  async function save(e) {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const body = { name: f.name, description: f.description, color: f.color, manager_id: Number(f.manager_id) || 0 };
      let id = team?.id;
      if (isNew) id = (await api("/teams", { method: "POST", body })).id;
      else await api(`/teams/${id}`, { method: "PATCH", body });
      if (!locked) {
        const ids = [...members];
        if (f.manager_id && !ids.includes(Number(f.manager_id))) ids.push(Number(f.manager_id));
        await api(`/teams/${id}/members`, { method: "PUT", body: { user_ids: ids } });
      }
      onSaved();
    } catch (e2) { setErr(e2.message); } finally { setBusy(false); }
  }
  async function remove() {
    if (!window.confirm(`Delete the team ${team.name}? Its board items keep their history but lose the team.`)) return;
    setBusy(true); setErr("");
    try { await api(`/teams/${team.id}`, { method: "DELETE" }); onSaved(); } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  return (
    <div className="modal-backdrop" role="presentation" onClick={() => !busy && onClose()}>
      <form className="hud modal editor" role="dialog" aria-modal="true" aria-label={isNew ? "New team" : `Edit ${team.name}`} onClick={(e) => e.stopPropagation()} onSubmit={save}>
        <header className="hud-head"><h2><span className="hud-code">TM</span>{isNew ? "New team" : team.name}</h2>
          <button type="button" className="btn ghost small" onClick={onClose} disabled={busy}>Close</button></header>
        <ErrorNote error={err} />
        <div className="form-grid">
          <label className="field"><span>Name</span><input autoFocus required value={f.name} maxLength={40} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
          <label className="field"><span>Manager (decides the team's requests)</span>
            <select value={f.manager_id} onChange={(e) => setF({ ...f, manager_id: e.target.value })}>
              <option value="">Nobody</option>{people.map((p) => <option key={p.id} value={p.id}>{p.display_name}</option>)}
            </select></label>
          <label className="field wide"><span>What the team does (optional)</span><input value={f.description} maxLength={200} onChange={(e) => setF({ ...f, description: e.target.value })} /></label>
          <div className="field wide"><span>Colour (its lane on the boards)</span>
            <div className="pick-chips">{COLORS.map((c) => (
              <button key={c} type="button" className={`team-swatch${f.color === c ? " on" : ""}`} style={{ "--c": c }} aria-label={c} aria-pressed={f.color === c} onClick={() => setF({ ...f, color: c })} />
            ))}</div>
          </div>
        </div>
        <div className="field"><span>Members{locked ? " (this group gives roles: change it under Settings → Roles)" : ""}</span>
          <div className="team-member-pick">
            {people.map((p) => (
              <label key={p.id} className={`team-member${members.has(p.id) ? " on" : ""}`}>
                <input type="checkbox" disabled={locked} checked={members.has(p.id)}
                       onChange={(e) => { const n = new Set(members); e.target.checked ? n.add(p.id) : n.delete(p.id); setMembers(n); }} />
                <span className="bd-avatar sm">{initials(p.display_name)}</span>{p.display_name}
              </label>
            ))}
          </div>
          <span className="muted tiny">People whose teams change are signed out of the dashboard once, so their access is fresh.</span>
        </div>
        <div className="modal-actions">
          {!isNew && !locked && <button type="button" className="btn danger small" onClick={remove} disabled={busy}>Delete team</button>}
          <button type="button" className="btn ghost small" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn primary small" disabled={busy || !f.name.trim()}>{isNew ? "Create team" : "Save"}</button>
        </div>
      </form>
    </div>
  );
}

export default function Teams({ onToast }) {
  const { data, error, reload } = usePoll(() => api("/teams"), 0);
  const [edit, setEdit] = useState(null);           // {} new, or a team
  const allPeople = data?.people || [];
  const teams = data?.teams || [];
  return (
    <div className="team-teams">
      <div className="mail-toolbar">
        <b className="mail-title">Teams</b>
        {data?.can_manage && <button className="btn primary small" onClick={() => setEdit({})}><Icon name="plus" className="btn-ico" />New team</button>}
      </div>
      <p className="muted small">Teams own work on the boards (each team gets its own lane) and their manager accepts or declines task requests.
        A team is a group of people: groups that also give roles are managed under Settings → Roles.</p>
      <ErrorNote error={error} />
      {data && teams.length === 0 && <Empty>No teams yet.{data.can_manage ? " Create the first one." : ""}</Empty>}
      <div className="team-team-list">
        {teams.map((t) => (
          <article key={t.id} className="team-team" style={{ "--team": t.color || "var(--muted)" }}>
            <header>
              <span className="bd-lane-dot" /><b>{t.name}</b>
              {t.mine && <Tag kind="ok">your team</Tag>}
              {t.gives_roles && <Tag>gives roles</Tag>}
              {data.can_manage && <button type="button" className="btn ghost small" onClick={() => setEdit(t)}>Edit</button>}
            </header>
            {t.description && <p className="muted small">{t.description}</p>}
            <p className="small">{t.manager_name ? <>Manager: <b>{t.manager_name}</b></> : <span className="muted">No manager yet: requests go to the board's managers.</span>}</p>
            <div className="team-team-members">
              {t.members.map((m) => <span key={m.id} className="bd-avatar" title={m.display_name}>{initials(m.display_name)}</span>)}
              {t.members.length === 0 && <span className="muted small">No members.</span>}
            </div>
          </article>
        ))}
      </div>
      {edit && <TeamEditor team={edit.id ? edit : null} people={allPeople} canRoles={data?.can_roles}
                           onClose={() => setEdit(null)} onSaved={() => { setEdit(null); reload(); onToast?.("Team saved"); }} />}
    </div>
  );
}
