import { useState } from "react";
import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { ErrorNote, Tag } from "./common.jsx";

/* roles.manage: preview the dashboard and the web chat as a role or as a person, to check permissions. */
export default function ViewAsCard({ me, onChanged }) {
  const { data, error } = usePoll(() => (me.can_preview ? api("/view-as") : Promise.resolve(null)));
  const [kind, setKind] = useState("role");
  const [pick, setPick] = useState("");
  const [err, setErr] = useState("");
  if (!me.can_preview) return null;
  const roles = data?.roles || [];
  const people = data?.people || [];
  const role = roles.find((r) => r.name === (pick || roles[0]?.name));
  const person = people.find((p) => String(p.id) === (pick || String(people[0]?.id || "")));

  async function go(body) {
    setErr("");
    try { await api("/view-as", { method: body ? "POST" : "DELETE", body }); onChanged?.(); } catch (e) { setErr(e.message); }
  }
  return (
    <div className="view-as">
      <h3 className="sub">View as {me.viewing_as && <Tag kind="busy">viewing as {me.viewing_as}</Tag>}</h3>
      <p className="muted small">
        See the dashboard, and talk to {me.assistant_name || "Alfred"} in the Chat tab, with a role's or a person's
        permissions (all their roles together), to check what they can see and do. It uses your own account's data,
        never shows more than you may see yourself, and you can switch back at any time.
      </p>
      <ErrorNote error={err || error} />
      {me.viewing_as ? (
        <button className="btn primary small" onClick={() => go(null)}>Back to my own view</button>
      ) : (
        <>
          <div className="field-row">
            <label className="field"><span>Preview</span>
              <select value={kind} onChange={(e) => { setKind(e.target.value); setPick(""); }}>
                <option value="role">a role</option>
                <option value="person">a person</option>
              </select>
            </label>
            {kind === "role" ? (
              <label className="field"><span>Role</span>
                <select value={role?.name || ""} onChange={(e) => setPick(e.target.value)}>
                  {roles.map((r) => <option key={r.name} value={r.name}>{r.label}</option>)}
                </select>
              </label>
            ) : (
              <label className="field"><span>Person</span>
                <select value={person?.id || ""} onChange={(e) => setPick(e.target.value)}>
                  {people.map((p) => <option key={p.id} value={p.id}>{p.display_name}</option>)}
                </select>
              </label>
            )}
            <button className="btn primary small" disabled={kind === "role" ? !role : !person}
                    onClick={() => go(kind === "role" ? { role: role.name } : { user_id: person.id })}>
              View as {kind === "role" ? role?.label : person?.display_name}
            </button>
          </div>
          {kind === "role" && role && (
            <p className="muted tiny">
              {role.description ? `${role.description.replace(/\.$/, "")}. ` : ""}Channels: {role.channels.join(", ") || "none"}
              {!role.channels.includes("dashboard") && " (no dashboard: you'll see the chat only)"}.
              {" "}{role.tools.length} of {(me.assistant_name || "Alfred")}'s tools.
            </p>
          )}
        </>
      )}
    </div>
  );
}
