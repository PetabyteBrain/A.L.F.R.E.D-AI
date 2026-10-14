import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { fmtTime } from "../format.js";
import { usePoll } from "../hooks.js";
import { Empty, ErrorNote, Tag } from "../components/common.jsx";
import Connections from "../components/Connections.jsx";
import CallDialog from "../components/CallDialog.jsx";
import { ConfirmDialog } from "../components/hud.jsx";
import { DataRequests, PersonData } from "../components/DataRequests.jsx";

/* Several roles per person, as toggles; the first one picked is the primary role. Roles that would
   give more than the editor has are greyed out (the server refuses them anyway). */
function RolePicker({ roles, value, onChange }) {
  const toggle = (name) => onChange(value.includes(name) ? value.filter((r) => r !== name) : [...value, name]);
  return (
    <div className="pick-chips" role="group" aria-label="Roles">
      {roles.map((r) => (
        <button key={r.name} type="button" aria-pressed={value.includes(r.name)}
                className={`chip${value.includes(r.name) ? "" : " off"}`}
                disabled={!r.assignable && !value.includes(r.name)}
                title={`${r.description || r.label} · channels: ${r.channels.join(", ") || "none"}`}
                onClick={() => toggle(r.name)}>
          {r.label}
        </button>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- editor (people.manage) */
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
    setForm({ display_name: view.display_name, roles: view.roles, active: view.active, notes: view.notes || "" });
    setLogin({ username: view.dashboard_username || "", password: "" });
  };
  const refresh = useCallback(() => api("/people")
    .then((all) => { const v = all.find((x) => x.id === personId); if (v) load(v); else setMsg({ ok: "", err: "Person not found." }); })
    .catch((e) => setMsg({ ok: "", err: e.message })), [personId]);
  useEffect(() => { refresh(); }, [refresh]);
  const changed = useCallback(() => { refresh(); onChanged(); }, [refresh, onChanged]);
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
  const canDashboard = p.channels.includes("dashboard");
  const dirty = form.display_name !== p.display_name || form.roles.join() !== p.roles.join()
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
          <div className="field wide"><span>Roles</span>
            <RolePicker roles={roles} value={form.roles} onChange={(v) => setForm({ ...form, roles: v })} />
          </div>
          <label className="field wide"><span>Notes (only people who manage Personnel see these)</span>
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
        <p className="muted tiny">
          Can reach {p.channels.length ? `them by ${p.channels.join(", ")}` : "no channel"}.
          {p.group_roles.length > 0 && <> From groups ({p.groups.map((g) => g.name).join(", ")}): {p.group_roles.join(", ")}.</>}
          {" "}Edit roles and groups on Settings → Roles.
        </p>

        <h3 className="sub">Connections</h3>
        <Connections
          connections={p.connections}
          busy={busy}
          base="/connections"
          canOverride
          onChanged={changed}
          onAdd={(type, value) => run(() => api(`/people/${p.id}/connections`, { method: "POST", body: { type, value } }), "Connection added.")}
          onUpdate={(id, value) => run(() => api(`/connections/${Number(id)}`, { method: "PATCH", body: { value } }), "Connection updated.")}
          onRemove={(c) => run(() => api(`/connections/${Number(c.id)}`, { method: "DELETE" }), "Connection removed.")}
        />

        <h3 className="sub">Dashboard login</h3>
        {!canDashboard && <p className="muted small">Their roles have no dashboard access.</p>}
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
              {p.mfa && !isSelf && (
                <button className="btn danger small" type="button" disabled={busy}
                        title="For a lost phone: they sign in with the password only and set it up again"
                        onClick={() => run(() => api(`/people/${p.id}/2fa/reset`, { method: "POST" }), "Two-factor sign-in reset; they were signed out.")}>Reset 2FA</button>
              )}
            </div>
          </form>
        )}
        {p.last_dashboard_seen && <p className="muted tiny">Last seen on the dashboard {fmtTime(p.last_dashboard_seen)}</p>}

        <PersonData person={p} isSelf={isSelf} onDone={(scope) => { if (scope === "account") { onChanged(); onClose(); } else changed(); }} />

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
  const [form, setForm] = useState({ display_name: "", roles: ["friend"], notes: "" });
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
      <div className="field wide"><span>Roles</span>
        <RolePicker roles={roles} value={form.roles} onChange={(v) => setForm({ ...form, roles: v })} />
      </div>
      <label className="field wide"><span>Notes (optional)</span>
        <input value={form.notes} maxLength={500} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
      </label>
      <div className="form-actions wide">
        <button className="btn primary small" type="submit" disabled={busy || !form.roles.length}>Create</button>
        <button className="btn ghost small" type="button" onClick={onCancel} disabled={busy}>Cancel</button>
      </div>
      {err && <p className="error wide">{err}</p>}
    </form>
  );
}

/* ---------------------------------------------------------------- access log (Team → Access log) */
export function AccessLog() {
  const { data: attempts, error } = usePoll(() => api("/access-attempts?limit=80"), 15000);
  return (
    <div className="team-accounts">
      <div className="mail-toolbar"><b className="mail-title">Access log</b></div>
      <p className="muted small">Who tried to reach Alfred: allowed people, and numbers or accounts that aren't in the registry (blocked).</p>
      <ErrorNote error={error} />
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
    </div>
  );
}

/* ---------------------------------------------------------------- accounts (Team → Accounts, people.manage) */
export default function PeopleAccounts({ assistant, me, openId = null, onOpened }) {
  const { data: people, error, reload } = usePoll(() => api("/people"), 30000);
  const { data: roles } = usePoll(() => api("/roles"));
  const [editing, setEditing] = useState(null);
  const [q, setQ] = useState("");
  useEffect(() => { if (openId) { setEditing(openId); onOpened?.(); } }, [openId, onOpened]);
  const [adding, setAdding] = useState(false);
  const [callee, setCallee] = useState(null);
  const [notice, setNotice] = useState("");
  const roleLabel = (name) => (roles || []).find((r) => r.name === name)?.label || name;

  const shown = (people || []).filter((p) => !q || `${p.display_name} ${p.dashboard_username || ""} ${p.connections.map((i) => i.value).join(" ")}`
    .toLowerCase().includes(q.trim().toLowerCase()));
  return (
    <div className="team-accounts">
      <div className="mail-toolbar">
        <div className="mail-filters"><b className="mail-title">Accounts</b>
          <form className="mail-search" onSubmit={(e) => e.preventDefault()}>
            <input value={q} maxLength={80} onChange={(e) => setQ(e.target.value)} placeholder="Name, login, number…" aria-label="Search accounts" />
          </form>
        </div>
        {!adding && <button className="btn primary small" onClick={() => setAdding(true)} disabled={!roles}>Add person</button>}
      </div>
      <p className="muted small">Everyone {assistant} knows: their roles, groups, phone numbers, Telegram and email, dashboard logins and
        two-factor sign-in. Their employee record (if they work here) is in the Directory.</p>
      <ErrorNote error={error} />
      {notice && <div className="notice" role="status" onClick={() => setNotice("")}>{notice}</div>}
      <DataRequests onChanged={reload} />
      {adding && roles && (
        <NewPerson roles={roles} onCancel={() => setAdding(false)}
                   onCreated={(p) => { setAdding(false); reload(); setEditing(p.id); }} />
      )}
      <div className="list">
        {shown.map((p) => (
          <div className={`row person-row${p.active ? "" : " inactive"}`} key={p.id}>
            <div>
              <div>
                #{p.id} {p.display_name} {p.roles.map((r) => <Tag key={r}>{roleLabel(r)}</Tag>)}{" "}
                {p.groups.map((g) => <Tag key={`g${g.id}`} kind="busy">{g.name}</Tag>)}{" "}
                {!p.active && <Tag kind="bad">disabled</Tag>}{" "}
                {p.dashboard_username && <Tag kind="ok">login: {p.dashboard_username}</Tag>}{" "}
                {p.dashboard_username && (p.mfa ? <Tag kind="ok">2FA</Tag> : <Tag>no 2FA</Tag>)}
              </div>
              <div className="meta">{p.connections.map((i) => `${i.type}: ${i.value}`).join(" · ") || "no connections"}</div>
            </div>
            <div className="row-actions">
              {p.active && p.connections.some((i) => i.type === "phone" && i.verified) && (
                <button className="btn small" onClick={() => setCallee(p)}>Call</button>
              )}
              <button className="btn primary small" onClick={() => setEditing(p.id)}>Edit</button>
            </div>
          </div>
        ))}
      </div>


      {editing && roles && (
        <PersonEditor personId={editing} roles={roles} me={me} onClose={() => setEditing(null)} onChanged={reload} />
      )}
      {callee && (
        <CallDialog
          assistant={assistant}
          targets={(people || []).filter((x) => x.active && x.connections.some((i) => i.type === "phone" && i.verified))}
          initialTarget={callee}
          onCancel={() => setCallee(null)}
          onDone={(r) => {
            setCallee(null);
            setNotice(r.held_for_quiet_hours ? `${r.target_name} is in quiet hours; the call goes out at ${r.run_at_local}.`
              : r.status === "pending" ? `Call to ${r.target_name} scheduled for ${r.run_at_local}.`
              : `Dialing ${r.target_name}… watch the Live tab.`);
          }}
        />
      )}
    </div>
  );
}
