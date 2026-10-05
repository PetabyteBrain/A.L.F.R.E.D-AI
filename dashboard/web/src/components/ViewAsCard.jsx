import { useState } from "react";
import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { ErrorNote, Tag } from "./common.jsx";

/* Admins: preview the dashboard and the web chat with another role's permissions. */
export default function ViewAsCard({ me, onChanged }) {
  const { data, error } = usePoll(() => api("/view-as"));
  const [role, setRole] = useState("");
  const [err, setErr] = useState("");
  if (me.real_role !== "admin") return null;
  const roles = data?.roles || [];
  const pick = roles.find((r) => r.name === (role || roles[0]?.name));

  async function go(path, body) {
    setErr("");
    try { await api("/view-as", { method: body ? "POST" : "DELETE", body }); onChanged?.(); } catch (e) { setErr(e.message); }
  }
  return (
    <div className="view-as">
      <h3 className="sub">View as role {me.viewing_as && <Tag kind="busy">viewing as {me.viewing_as}</Tag>}</h3>
      <p className="muted small">
        See the dashboard, and talk to Alfred in the Chat tab, with another role's permissions, to check what they can
        see and do. It uses your own account's data with that role's rules; you can switch back at any time.
      </p>
      <ErrorNote error={err || error} />
      {me.viewing_as ? (
        <button className="btn primary small" onClick={() => go("/view-as")}>Back to my admin view</button>
      ) : (
        <>
          <div className="field-row">
            <label className="field"><span>Role</span>
              <select value={role || roles[0]?.name || ""} onChange={(e) => setRole(e.target.value)}>
                {roles.map((r) => <option key={r.name} value={r.name}>{r.name}</option>)}
              </select>
            </label>
            <button className="btn primary small" disabled={!pick} onClick={() => go("/view-as", { role: pick.name })}>View as {pick?.name}</button>
          </div>
          {pick && (
            <p className="muted tiny">
              {pick.description ? `${pick.description}. ` : ""}Channels: {pick.channels.join(", ") || "none"}
              {!pick.channels.includes("dashboard") && " (no dashboard: you'll see the chat only)"}.
              Alfred's tools for this role: {pick.tools.join(", ")}.
            </p>
          )}
        </>
      )}
    </div>
  );
}
