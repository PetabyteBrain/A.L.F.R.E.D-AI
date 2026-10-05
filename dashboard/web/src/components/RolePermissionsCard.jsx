import { useState } from "react";
import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { ErrorNote, Tag } from "./common.jsx";

/* Admin (Settings tab): switches that narrow what a role may do (orchestrator/permissions.py). */
export default function RolePermissionsCard({ assistant }) {
  const { data, error, reload } = usePoll(() => api("/settings/role-permissions"));
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  if (!data) return <ErrorNote error={error} />;

  async function flip(role, permission, allowed) {
    setBusy(`${role}:${permission}`); setErr("");
    try { await api("/settings/role-permissions", { method: "PUT", body: { role, permission, allowed } }); await reload(); }
    catch (e) { setErr(e.message); } finally { setBusy(""); }
  }
  return (
    <section className="settings-block">
      <h3 className="sub">Role permissions</h3>
      <p className="muted small">
        What each role may do on top of its basics, in the dashboard and when they ask {assistant}. Admins always
        have everything; deleting n8n workflows stays admin-only. Changes apply right away.
      </p>
      <ErrorNote error={err} />
      {Object.entries(data.roles).map(([role, perms]) => (
        <div key={role} className="perm-role">
          <div className="note-folder">{role}s</div>
          {Object.entries(perms).map(([p, on]) => (
            <label key={p} className="toggle perm-row">
              <input type="checkbox" checked={on} disabled={busy === `${role}:${p}`} onChange={(e) => flip(role, p, e.target.checked)} />
              <span className="perm-label">{data.labels[p] || p}</span>
              {on ? <Tag kind="ok">on</Tag> : <Tag>off</Tag>}
            </label>
          ))}
        </div>
      ))}
      <p className="muted tiny">Check the result with "View as role" below.</p>
    </section>
  );
}
