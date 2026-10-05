import { useState } from "react";
import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { Empty, ErrorNote, Tag } from "./common.jsx";
import { CategorySelect } from "./categories.jsx";

const FOLLOW = { none: "No follow-up", telegram: "Telegram check-in", call: "Phone check-in" };

function inTwoDays() {
  const d = new Date(Date.now() + 2 * 864e5);
  d.setHours(12, 0, 0, 0);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T12:00`;
}

/* Tasks with owners, due times and follow-ups. `targets`: people this user may assign tasks to. */
export default function TaskList({ me, assistant }) {
  const { data: t, error, reload } = usePoll(() => api("/tasks"), 20000);
  const { data: targets } = usePoll(() => (me.can_request_calls ? api("/calls/targets") : Promise.resolve([])));
  const blank = { title: "", owner_id: "", due: "", follow_up: "none", notes: "" };
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function run(fn) {
    setBusy(true);
    setErr("");
    try { await fn(); await reload(); return true; } catch (e) { setErr(e.message); return false; } finally { setBusy(false); }
  }
  const save = async (e) => {
    e.preventDefault();
    const ok = await run(() => api("/tasks", { method: "POST", body: {
      title: form.title, owner_id: form.owner_id ? Number(form.owner_id) : null, due: form.due || null,
      follow_up: form.due ? form.follow_up : "none", notes: form.notes } }));
    if (ok) setForm(null);
  };
  const canEdit = (x) => me.role === "admin" || [x.owner_user_id, x.requested_by].includes(me.id) || (!x.owner_user_id && me.can_request_calls);
  const others = (targets || []).filter((p) => p.id !== me.id);
  const rows = t?.local || [];

  return (
    <>
      <h3 className="sub">
        Tasks
        {!form && <button className="btn primary small" onClick={() => setForm(blank)}>New task</button>}
      </h3>
      <ErrorNote error={error || err} />
      {form && (
        <form className="form-grid reminder-form" onSubmit={save}>
          <label className="field wide"><span>Task</span>
            <input value={form.title} maxLength={200} required autoFocus placeholder="Send the invoice to the client"
                   onChange={(e) => setForm({ ...form, title: e.target.value })} />
          </label>
          {others.length > 0 && (
            <label className="field"><span>Who</span>
              <select value={form.owner_id} onChange={(e) => setForm({ ...form, owner_id: e.target.value })}>
                <option value="">Me</option>
                {others.map((p) => <option key={p.id} value={p.id}>{p.display_name}</option>)}
              </select>
            </label>
          )}
          <label className="field"><span>Due (optional)</span>
            <input type="datetime-local" value={form.due}
                   onChange={(e) => setForm({ ...form, due: e.target.value, follow_up: e.target.value && form.follow_up === "none" ? "telegram" : form.follow_up })}
                   onFocus={() => !form.due && setForm({ ...form, due: inTwoDays(), follow_up: form.follow_up === "none" ? "telegram" : form.follow_up })} />
          </label>
          {form.due && (
            <label className="field"><span>At the due time</span>
              <select value={form.follow_up} onChange={(e) => setForm({ ...form, follow_up: e.target.value })}>
                {Object.entries(FOLLOW).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </label>
          )}
          <label className="field wide"><span>Notes (optional)</span>
            <input value={form.notes} maxLength={500} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
          </label>
          {form.due && form.follow_up !== "none" && (
            <p className="muted tiny wide">
              {assistant} asks {form.owner_id ? "them" : "you"} whether it's done at the due time, again daily up to three times
              {form.owner_id ? ", and tells you the answer." : "."} Quiet hours are respected.
            </p>
          )}
          <div className="form-actions wide">
            <button className="btn ghost small" type="button" onClick={() => setForm(null)}>Cancel</button>
            <button className="btn primary small" type="submit" disabled={busy || !form.title.trim()}>Add task</button>
          </div>
        </form>
      )}
      {t && rows.length === 0 && !form && <Empty>No open tasks. Tell {assistant} "ask Max to send the invoice by Friday and follow up".</Empty>}
      <div className="list">
        {rows.map((x) => (
          <div className="row" key={x.id}>
            <div>
              <div>
                📋 {x.title}{" "}
                {x.follow_ups > 0 && x.status === "active" && <Tag kind="busy">asked {Math.min(x.follow_ups, 3)}×</Tag>}
              </div>
              <div className="meta">
                {x.owner_name ? (x.owner_user_id === me.id ? "you" : x.owner_name) : "shared"}
                {x.requester_name && x.requested_by !== x.owner_user_id ? ` · from ${x.requested_by === me.id ? "you" : x.requester_name}` : ""}
                {x.due_local ? <> · due <span className="mono">{x.due_local}</span></> : x.schedule ? ` · ${x.schedule}` : ""}
                {x.follow_up !== "none" && x.follow_up_local ? ` · next check-in ${x.follow_up_local} (${x.follow_up})` : ""}
              </div>
              {x.notes && <div className="meta">{x.notes}</div>}
            </div>
            {canEdit(x) && (
              <div className="row-actions">
                <CategorySelect kind="task" id={x.id} value={x.category} onChanged={reload} />
                <button className="btn small" disabled={busy} onClick={() => run(() => api(`/tasks/${Number(x.id)}/done`, { method: "POST", body: {} }))}>Done</button>
                <button className="btn ghost small" disabled={busy} onClick={() => run(() => api(`/tasks/${Number(x.id)}`, { method: "DELETE" }))}>Cancel</button>
              </div>
            )}
          </div>
        ))}
      </div>
    </>
  );
}
