import { useEffect, useState } from "react";
import { api } from "../api.js";
import { fmtTime } from "../format.js";
import { usePoll } from "../hooks.js";
import { Empty, ErrorNote, Tag } from "../components/common.jsx";
import Connections from "../components/Connections.jsx";
import CallDialog from "../components/CallDialog.jsx";
import { ConfirmDialog, Panel } from "../components/hud.jsx";

/* ---------------------------------------------------------------- editor (admin) */
function PersonEditor({ personId, roles, me, onClose, onChanged }) {
  const [p, setP] = useState(null);
  const [form, setForm] = useState(null);
  const [login, setLogin] = useState({ username: "", password: "" });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState({ ok: "", err: "" });
  const [confirmDelete, setConfirmDelete] = useState(false);
  const isSelf = personId === me.id;

  const load = (view) => {
    setP(view);
    setForm({ display_name: view.display_name, role: view.role, active: view.active, notes: view.notes || "" });
    setLogin({ username: view.dashboard_username || "", password: "" });
  };
  useEffect(() => {
    api("/people")
      .then((all) => { const v = all.find((x) => x.id === personId); if (v) load(v); else setMsg({ ok: "", err: "Person not found." }); })
      .catch((e) => setMsg({ ok: "", err: e.message }));
  }, [personId]);
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && !busy && !confirmDelete) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, confirmDelete, onClose]);

  async function run(fn, okText) {
    setBusy(true);
    setMsg({ ok: "", err: "" });
    try {
      const view = await fn();
      if (view && view.id) load(view);
      setMsg({ ok: okText, err: "" });
      onChanged();
      return true;
    } catch (e) {
      setMsg({ ok: "", err: e.message });
      return false;
    } finally {
      setBusy(false);
    }
  }

  if (!p || !form) {
    return (
      <div className="modal-backdrop" role="presentation" onClick={onClose}>
        <div className="hud modal editor"><ErrorNote error={msg.err} />{!msg.err && <p className="muted">Loading…</p>}</div>
      </div>
    );
  }
  const role = roles.find((r) => r.name === form.role);
  const canDashboard = role?.channels.includes("dashboard");
  const dirty = form.display_name !== p.display_name || form.role !== p.role
    || form.active !== p.active || form.notes !== (p.notes || "");

  return (
    <div className="modal-backdrop" role="presentation" onClick={() => !busy && onClose()}>
      <div className="hud modal editor" role="dialog" aria-modal="true" aria-labelledby="ed-title" onClick={(e) => e.stopPropagation()}>
        <header className="hud-head">
          <h2 id="ed-title"><span className="hud-code">#{p.id}</span>{p.display_name}{isSelf && <Tag>you</Tag>}</h2>
          <button className="btn ghost small" onClick={onClose} disabled={busy}>Close</button>
        </header>
        {msg.ok && <div className="notice" role="status">{msg.ok}</div>}
        <ErrorNote error={msg.err} />

        <h3 className="sub flush">Person</h3>
        <form className="form-grid" onSubmit={(e) => { e.preventDefault(); run(() => api(`/people/${p.id}`, { method: "PATCH", body: form }), "Saved."); }}>
          <label className="field"><span>Name</span>
            <input value={form.display_name} maxLength={80} required onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
          </label>
          <label className="field"><span>Role</span>
            <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              {roles.map((r) => <option key={r.name} value={r.name}>{r.name} — {r.channels.join(", ")}</option>)}
            </select>
          </label>
          <label className="field wide"><span>Notes (only admins see these)</span>
            <input value={form.notes} maxLength={500} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
          </label>
          <label className="toggle wide">
            <input type="checkbox" checked={form.active} onChange={(e) => setForm({ ...form, active: e.target.checked })} />
            <span>{form.active ? "Active: can reach the assistant" : "Disabled: ignored on every channel"}</span>
          </label>
          <div className="form-actions wide">
            <button className="btn primary small" type="submit" disabled={busy || !dirty}>Save changes</button>
          </div>
        </form>
        {role && <p className="muted tiny">{role.description}</p>}

        <h3 className="sub">Connections</h3>
        <Connections
          connections={p.connections}
          busy={busy}
          onAdd={(type, value) => run(() => api(`/people/${p.id}/connections`, { method: "POST", body: { type, value } }), "Connection added.")}
          onUpdate={(id, value) => run(() => api(`/connections/${Number(id)}`, { method: "PATCH", body: { value } }), "Connection updated.")}
          onRemove={(c) => run(() => api(`/connections/${Number(c.id)}`, { method: "DELETE" }), "Connection removed.")}
        />

        <h3 className="sub">Dashboard login</h3>
        {!canDashboard && <p className="muted small">The <b>{form.role}</b> role has no dashboard access.</p>}
        {canDashboard && (
          <form className="form-grid" onSubmit={async (e) => {
            e.preventDefault();
            const ok = await run(() => api(`/people/${p.id}/login`, { method: "PUT",
              body: { username: login.username, password: login.password || null } }),
            login.password ? "Login saved with the new password. Their other sessions were signed out." : "Username saved.");
            if (ok) setLogin((l) => ({ ...l, password: "" }));
          }}>
            <label className="field"><span>Username</span>
              <input value={login.username} maxLength={32} required autoComplete="off" spellCheck={false}
                     onChange={(e) => setLogin({ ...login, username: e.target.value.toLowerCase() })} />
            </label>
            <label className="field"><span>{p.dashboard_username ? "New password (leave empty to keep)" : "Password (min. 10)"}</span>
              <input type="password" value={login.password} maxLength={200} autoComplete="new-password"
                     required={!p.dashboard_username} minLength={10}
                     onChange={(e) => setLogin({ ...login, password: e.target.value })} />
            </label>
            <div className="form-actions wide">
              <button className="btn primary small" type="submit" disabled={busy}>{p.dashboard_username ? "Update login" : "Create login"}</button>
              {p.dashboard_username && !isSelf && (
                <button className="btn danger small" type="button" disabled={busy}
                        onClick={() => run(() => api(`/people/${p.id}/login`, { method: "DELETE" }), "Login removed.")}>Remove login</button>
              )}
            </div>
          </form>
        )}
        {p.last_dashboard_seen && <p className="muted tiny">Last seen on the dashboard {fmtTime(p.last_dashboard_seen)}</p>}

        {!isSelf && (
          <>
            <h3 className="sub">Danger zone</h3>
            <p className="muted small">Deleting only works for people without call or chat history. Otherwise, disable them above.</p>
            <button className="btn danger small" disabled={busy} onClick={() => setConfirmDelete(true)}>Delete {p.display_name}</button>
          </>
        )}
        {confirmDelete && (
          <ConfirmDialog
            title="Delete person"
            body={<>Permanently delete <b>{p.display_name}</b> and all their connections?</>}
            confirmLabel="Delete"
            busy={busy}
            error={msg.err}
            onConfirm={async () => {
              if (await run(() => api(`/people/${p.id}`, { method: "DELETE" }), "Deleted.")) { setConfirmDelete(false); onClose(); }
            }}
            onCancel={() => setConfirmDelete(false)}
          />
        )}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- new person */
function NewPerson({ roles, onCreated, onCancel }) {
  const [form, setForm] = useState({ display_name: "", role: "friend", notes: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      onCreated(await api("/people", { method: "POST", body: form }));
    } catch (e2) {
      setErr(e2.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="form-grid new-person" onSubmit={submit}>
      <label className="field"><span>Name</span>
        <input value={form.display_name} maxLength={80} required autoFocus onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
      </label>
      <label className="field"><span>Role</span>
        <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
          {roles.map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
        </select>
      </label>
      <label className="field wide"><span>Notes (optional)</span>
        <input value={form.notes} maxLength={500} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
      </label>
      <div className="form-actions wide">
        <button className="btn primary small" type="submit" disabled={busy}>Create</button>
        <button className="btn ghost small" type="button" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
      {err && <p className="error wide">{err}</p>}
    </form>
  );
}

/* ---------------------------------------------------------------- page */
export default function People({ assistant, me }) {
  const { data: people, error, reload } = usePoll(() => api("/people"), 30000);
  const { data: roles } = usePoll(() => api("/roles"));
  const { data: attempts, error: err2 } = usePoll(() => api("/access-attempts?limit=80"), 15000);
  const [editing, setEditing] = useState(null);
  const [adding, setAdding] = useState(false);
  const [callee, setCallee] = useState(null);
  const [notice, setNotice] = useState("");

  return (
    <Panel code="07" title="Personnel registry" actions={
      !adding && <button className="btn primary small" onClick={() => setAdding(true)} disabled={!roles}>Add person</button>
    }>
      <ErrorNote error={error || err2} />
      {notice && <div className="notice" role="status" onClick={() => setNotice("")}>{notice}</div>}
      {adding && roles && (
        <NewPerson roles={roles} onCancel={() => setAdding(false)}
                   onCreated={(p) => { setAdding(false); reload(); setEditing(p.id); }} />
      )}
      <div className="list">
        {(people || []).map((p) => (
          <div className={`row person-row${p.active ? "" : " inactive"}`} key={p.id}>
            <div>
              <div>
                #{p.id} {p.display_name} <Tag>{p.role}</Tag>{" "}
                {!p.active && <Tag kind="bad">disabled</Tag>}{" "}
                {p.dashboard_username && <Tag kind="ok">login: {p.dashboard_username}</Tag>}
              </div>
              <div className="meta">{p.connections.map((i) => `${i.type}: ${i.value}`).join(" · ") || "no connections"}</div>
            </div>
            <div className="row-actions">
              {p.active && p.connections.some((i) => i.type === "phone") && (
                <button className="btn small" onClick={() => setCallee(p)}>Call</button>
              )}
              <button className="btn primary small" onClick={() => setEditing(p.id)}>Edit</button>
            </div>
          </div>
        ))}
      </div>

      <h3 className="sub">Access attempts</h3>
      {attempts && attempts.length === 0 && <Empty>No attempts recorded.</Empty>}
      <div className="feed">
        {(attempts || []).map((a, i) => (
          <div className="e" key={i}>
            <span className="ts">{fmtTime(a.ts)}</span>
            <span className={`kind ${a.resolved ? "" : "access"}`}>{a.resolved ? "allowed" : "blocked"}</span>
            <span>{a.channel} · {a.raw_identifier}{a.detail ? ` · ${a.detail}` : ""}</span>
          </div>
        ))}
      </div>

      {editing && roles && (
        <PersonEditor personId={editing} roles={roles} me={me} onClose={() => setEditing(null)} onChanged={reload} />
      )}
      {callee && (
        <CallDialog
          assistant={assistant}
          targets={(people || []).filter((x) => x.active && x.connections.some((i) => i.type === "phone"))}
          initialTarget={callee}
          onCancel={() => setCallee(null)}
          onDone={(r) => {
            setCallee(null);
            setNotice(r.status === "pending" ? `Call to ${r.target_name} scheduled for ${r.run_at_local}.`
              : `Dialing ${r.target_name}… watch the Live tab.`);
          }}
        />
      )}
    </Panel>
  );
}
