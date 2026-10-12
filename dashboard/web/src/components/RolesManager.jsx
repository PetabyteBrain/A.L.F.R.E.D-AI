import { useMemo, useState } from "react";
import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { ErrorNote, Tag } from "./common.jsx";

/* Settings → Roles (roles.manage): roles as editable sets of permissions, groups, and the explainer.
   All rules are enforced by the server (orchestrator/permissions.py); this only shows them. */

const EFFECTS = [["allow", "Allow"], [null, "—"], ["deny", "Deny"]];

function RoleEditor({ role, data, run, busy }) {
  const [label, setLabel] = useState(role.label);
  const [desc, setDesc] = useState(role.description || "");
  const mine = new Set(data.mine);
  const effective = new Set(role.effective);
  const groups = useMemo(() => {
    const out = [];
    for (const p of data.catalogue) {
      let g = out.find((x) => x.name === p.group);
      if (!g) out.push(g = { name: p.group, items: [] });
      g.items.push(p);
    }
    return out;
  }, [data.catalogue]);
  const others = data.roles.filter((r) => r.name !== role.name && !r.archived && r.name !== "admin");
  const path = `/access/roles/${encodeURIComponent(role.name)}`;
  const locked = role.fixed || role.archived;

  return (
    <div className="role-editor">
      {role.fixed ? (
        <p className="muted small">The admin role is fixed: it always has every permission. Give it to people on Personnel.</p>
      ) : (
        <form className="form-grid" onSubmit={(e) => { e.preventDefault(); run(path, "PATCH", { label, description: desc }, "Saved."); }}>
          <label className="field"><span>Name</span>
            <input value={label} maxLength={40} required disabled={locked} onChange={(e) => setLabel(e.target.value)} />
          </label>
          <label className="field"><span>Inherits from</span>
            <select value={role.parent || ""} disabled={locked || busy}
                    onChange={(e) => run(path, "PATCH", { parent: e.target.value || null }, "Saved.")}>
              <option value="">— no parent —</option>
              {others.map((r) => <option key={r.name} value={r.name}>{r.label}</option>)}
            </select>
          </label>
          <label className="field wide"><span>Description</span>
            <input value={desc} maxLength={200} disabled={locked} onChange={(e) => setDesc(e.target.value)} />
          </label>
          <div className="form-actions wide">
            <button className="btn primary small" type="submit"
                    disabled={busy || locked || (label === role.label && desc === (role.description || ""))}>Save</button>
            <button className="btn small" type="button" disabled={busy}
                    onClick={() => run(path, "PATCH", { archived: !role.archived }, role.archived ? "Restored." : "Archived.")}>
              {role.archived ? "Restore" : "Archive"}
            </button>
          </div>
        </form>
      )}
      {!role.fixed && (
        <p className="muted tiny">
          Allow adds a permission, Deny takes it away even if another role or the parent allows it, — leaves it to the
          other roles. {data.admin ? "" : "You can only change permissions you have yourself."}
        </p>
      )}
      {groups.map((g) => (
        <div key={g.name} className="perm-group">
          <div className="note-folder">{g.name}</div>
          {g.items.map((p) => {
            const rule = role.fixed ? "allow" : (role.rules[p.key] ?? null);
            const inherited = !rule && effective.has(p.key);
            const blocked = locked || (!data.admin && !mine.has(p.key));
            return (
              <div key={p.key} className="perm-row">
                <span className="perm-label">{p.label}
                  {inherited && <Tag kind="ok">from {data.roles.find((r) => r.name === role.parent)?.label || "parent"}</Tag>}
                  {rule === "allow" && !effective.has(p.key) && !role.fixed && <Tag kind="bad">denied by parent</Tag>}
                </span>
                <div className="seg perm-seg" role="radiogroup" aria-label={p.label}>
                  {EFFECTS.map(([eff, text]) => (
                    <button key={text} type="button" role="radio" aria-checked={rule === eff}
                            className={rule === eff ? `active ${eff || "unset"}` : ""} disabled={blocked || busy}
                            onClick={() => rule !== eff && run(`${path}/rules`, "PUT", { permission: p.key, effect: eff })}>
                      {text}
                    </button>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

function NewRole({ data, run, busy, onCreated }) {
  const [label, setLabel] = useState("");
  const [copy, setCopy] = useState("");
  return (
    <form className="field-row" onSubmit={async (e) => {
      e.preventDefault();
      const out = await run("/access/roles", "POST", { label, copy_from: copy || null }, "Role created.");
      if (out?.created) { setLabel(""); onCreated(out.created); }
    }}>
      <label className="field"><span>New role</span>
        <input value={label} maxLength={40} required placeholder="e.g. Intern" onChange={(e) => setLabel(e.target.value)} />
      </label>
      <label className="field"><span>Start from</span>
        <select value={copy} onChange={(e) => setCopy(e.target.value)}>
          <option value="">nothing</option>
          {data.roles.filter((r) => !r.archived && !r.fixed).map((r) => <option key={r.name} value={r.name}>copy of {r.label}</option>)}
        </select>
      </label>
      <button className="btn primary small" type="submit" disabled={busy || !label.trim()}>Create</button>
    </form>
  );
}

function Roles({ data, run, busy }) {
  const [pick, setPick] = useState("cofounder");
  const role = data.roles.find((r) => r.name === pick) || data.roles[0];
  const tree = useMemo(() => {
    const kids = (name) => data.roles.filter((r) => (r.parent || null) === name && !r.archived);
    const out = [];
    const walk = (r, depth) => { out.push({ r, depth }); kids(r.name).forEach((k) => walk(k, depth + 1)); };
    data.roles.filter((r) => !r.archived && (!r.parent || !data.roles.some((x) => x.name === r.parent && !x.archived)))
      .forEach((r) => walk(r, 0));
    return [...out, ...data.roles.filter((r) => r.archived).map((r) => ({ r, depth: 0 }))];
  }, [data.roles]);
  return (
    <div className="roles-layout">
      <div className="roles-list">
        {tree.map(({ r, depth }) => (
          <button key={r.name} className={`role-item${r.name === role?.name ? " active" : ""}`}
                  style={{ paddingLeft: 10 + depth * 16 }} onClick={() => setPick(r.name)}>
            <span>{depth > 0 && "↳ "}{r.label}</span>
            <span className="meta">{r.archived ? "archived" : `${r.people} ${r.people === 1 ? "person" : "people"}`}</span>
          </button>
        ))}
        <NewRole data={data} run={run} busy={busy} onCreated={setPick} />
      </div>
      {role && <RoleEditor key={`${role.name}:${role.label}:${role.description}`} role={role} data={data} run={run} busy={busy} />}
    </div>
  );
}

function Groups({ data, run, busy }) {
  const [name, setName] = useState("");
  const roles = data.roles.filter((r) => !r.archived && !r.fixed);
  const active = data.people.filter((p) => p.active);
  return (
    <div>
      <p className="muted small">
        A group is a set of people (a team, a project). Everyone in it gets the group's roles on top of their own.
      </p>
      <form className="field-row" onSubmit={async (e) => {
        e.preventDefault();
        if (await run("/access/groups", "POST", { name }, "Group created.")) setName("");
      }}>
        <label className="field"><span>New group</span>
          <input value={name} maxLength={40} required placeholder="e.g. Sales" onChange={(e) => setName(e.target.value)} />
        </label>
        <button className="btn primary small" type="submit" disabled={busy || !name.trim()}>Create</button>
      </form>
      {data.groups.length === 0 && <p className="muted small">No groups yet.</p>}
      {data.groups.map((g) => {
        const members = new Set(g.members.map((m) => m.id));
        const groles = new Set(g.roles);
        const toggle = (set, v) => { const n = new Set(set); n.has(v) ? n.delete(v) : n.add(v); return [...n]; };
        return (
          <div key={g.id} className="group-card">
            <div className="group-head">
              <b>{g.name}</b> <span className="meta">{g.members.length} {g.members.length === 1 ? "member" : "members"}</span>
              <button className="btn ghost small" disabled={busy} onClick={() => {
                const n = window.prompt("Rename group", g.name);
                if (n && n.trim() && n !== g.name) run(`/access/groups/${g.id}`, "PATCH", { name: n }, "Renamed.");
              }}>Rename</button>
              <button className="btn danger small" disabled={busy}
                      onClick={() => window.confirm(`Delete the group ${g.name}?`) && run(`/access/groups/${g.id}`, "DELETE", null, "Group deleted.")}>Delete</button>
            </div>
            <div className="chips-label">Roles</div>
            <div className="pick-chips">
              {roles.map((r) => (
                <button key={r.name} type="button" className={`chip${groles.has(r.name) ? "" : " off"}`} disabled={busy}
                        aria-pressed={groles.has(r.name)}
                        onClick={() => run(`/access/groups/${g.id}/roles`, "PUT", { roles: toggle(groles, r.name) }, "Roles saved.")}>
                  {r.label}
                </button>
              ))}
            </div>
            <div className="chips-label">Members</div>
            <div className="pick-chips">
              {active.map((p) => (
                <button key={p.id} type="button" className={`chip${members.has(p.id) ? "" : " off"}`} disabled={busy}
                        aria-pressed={members.has(p.id)}
                        onClick={() => run(`/access/groups/${g.id}/members`, "PUT", { user_ids: toggle(members, p.id) }, "Members saved.")}>
                  {p.display_name}
                </button>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Explain({ data }) {
  const [who, setWho] = useState("");
  const [perm, setPerm] = useState("");
  const [person, setPerson] = useState(null);
  const [holders, setHolders] = useState(null);
  const [err, setErr] = useState("");
  const label = (name) => data.roles.find((r) => r.name === name)?.label || name;
  async function load(kind, value) {
    setErr("");
    try {
      if (kind === "person") { setWho(value); setPerson(value ? await api(`/access/explain/person/${Number(value)}`) : null); }
      else { setPerm(value); setHolders(value ? await api(`/access/explain/permission/${encodeURIComponent(value)}`) : null); }
    } catch (e) { setErr(e.message); }
  }
  return (
    <div className="explain-grid">
      <ErrorNote error={err} />
      <div>
        <label className="field"><span>What can … do?</span>
          <select value={who} onChange={(e) => load("person", e.target.value)}>
            <option value="">pick a person</option>
            {data.people.filter((p) => p.active).map((p) => <option key={p.id} value={p.id}>{p.display_name}</option>)}
          </select>
        </label>
        {person && (
          <>
            <p className="muted small">
              Roles: {person.roles.map(label).join(", ") || "none"}
              {person.group_roles.length > 0 && <> · from groups: {person.group_roles.map(label).join(", ")}</>}
              {person.inherited.length > 0 && <> · inherited: {person.inherited.map(label).join(", ")}</>}
            </p>
            <ul className="explain-list">
              {person.permissions.map((p) => (
                <li key={p.key} className={p.allowed ? "" : "off"}>
                  {p.allowed ? <Tag kind="ok">yes</Tag> : p.denied ? <Tag kind="bad">denied</Tag> : <Tag>no</Tag>} {p.label}
                  {p.because.length > 0 && <span className="meta"> · {p.because.map(label).join(", ")}</span>}
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
      <div>
        <label className="field"><span>Who can …?</span>
          <select value={perm} onChange={(e) => load("perm", e.target.value)}>
            <option value="">pick a permission</option>
            {data.catalogue.map((p) => <option key={p.key} value={p.key}>{p.group}: {p.label}</option>)}
          </select>
        </label>
        {holders && (holders.length ? (
          <ul className="explain-list">{holders.map((h) => <li key={h.id}>{h.display_name}</li>)}</ul>
        ) : <p className="muted small">Nobody (active).</p>)}
      </div>
    </div>
  );
}

export default function RolesManager() {
  const { data, error, reload } = usePoll(() => api("/access"));
  const [tab, setTab] = useState("roles");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState({ ok: "", err: "" });
  const state = data;

  async function run(path, method, body, okText = "") {
    setBusy(true); setMsg({ ok: "", err: "" });
    try {
      const out = await api(path, { method, body: body ?? undefined });
      await reload();
      setMsg({ ok: okText, err: "" });
      return out;
    } catch (e) { setMsg({ ok: "", err: e.message }); return null; } finally { setBusy(false); }
  }
  if (!state) return <ErrorNote error={error} />;
  return (
    <section className="settings-block roles-manager">
      <h3 className="sub">Roles and groups</h3>
      <p className="muted small">
        Each person can have several roles; their permissions add up, and a Deny always wins. Changes apply right away;
        people whose roles or groups change are signed out of the dashboard. Every change is logged.
      </p>
      <div className="seg roles-tabs" role="tablist">
        {[["roles", "Roles"], ["groups", "Groups"], ["explain", "Who can what"]].map(([id, text]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{text}</button>
        ))}
      </div>
      {msg.ok && <div className="notice" role="status">{msg.ok}</div>}
      <ErrorNote error={msg.err} />
      {tab === "roles" && <Roles data={state} run={run} busy={busy} />}
      {tab === "groups" && <Groups data={state} run={run} busy={busy} />}
      {tab === "explain" && <Explain data={state} />}
    </section>
  );
}
