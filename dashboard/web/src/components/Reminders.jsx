import { useState } from "react";
import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { Empty, ErrorNote, Tag } from "./common.jsx";

const STATUS_KIND = { pending: "", sending: "busy", sent: "busy", done: "ok", failed: "bad", cancelled: "" };
const STATUS_LABEL = { sent: "awaiting done" };
const REPEAT = { none: "Once", daily: "Every day", weekdays: "Weekdays", weekly: "Every week", monthly: "Every month" };

// datetime-local wants "YYYY-MM-DDTHH:MM" in local time.
function inAnHour() {
  const d = new Date(Date.now() + 60 * 60 * 1000);
  d.setSeconds(0, 0);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/* Open reminders (own + set for others; admins see everyone's) with a quick "new reminder" form. */
export default function Reminders({ me, assistant }) {
  const { data: rows, error, reload } = usePoll(() => api("/reminders"), 15000);
  const { data: targets } = usePoll(() => (me.can_request_calls ? api("/calls/targets") : Promise.resolve([])));
  const blank = { text: "", when: inAnHour(), recurrence: "none", channel: "telegram", escalate: false, person_id: "" };
  const [form, setForm] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  async function save(e) {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      await api("/reminders", { method: "POST", body: { ...form, person_id: form.person_id ? Number(form.person_id) : null } });
      setForm(null);
      reload();
    } catch (x) {
      setErr(x.message);
    } finally {
      setBusy(false);
    }
  }

  async function cancel(id) {
    setErr("");
    try {
      await api(`/reminders/${Number(id)}`, { method: "DELETE" });
      reload();
    } catch (x) {
      setErr(x.message);
    }
  }

  const others = (targets || []).filter((t) => t.id !== me.id);
  return (
    <>
      <h3 className="sub">
        Reminders
        {!form && <button className="btn primary small" onClick={() => setForm(blank)}>New reminder</button>}
      </h3>
      <ErrorNote error={error || err} />
      {form && (
        <form className="form-grid reminder-form" onSubmit={save}>
          <label className="field wide"><span>Remind about</span>
            <input value={form.text} maxLength={500} required autoFocus placeholder="Call the bank about the card"
                   onChange={(e) => setForm({ ...form, text: e.target.value })} />
          </label>
          <label className="field"><span>When</span>
            <input type="datetime-local" value={form.when} required onChange={(e) => setForm({ ...form, when: e.target.value })} />
          </label>
          <label className="field"><span>Repeat</span>
            <select value={form.recurrence} onChange={(e) => setForm({ ...form, recurrence: e.target.value })}>
              {Object.entries(REPEAT).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
          <label className="field"><span>How</span>
            <select value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value })}>
              <option value="telegram">Telegram message</option>
              <option value="call">Phone call</option>
            </select>
          </label>
          {others.length > 0 && (
            <label className="field"><span>For</span>
              <select value={form.person_id} onChange={(e) => setForm({ ...form, person_id: e.target.value })}>
                <option value="">Me</option>
                {others.map((t) => <option key={t.id} value={t.id}>{t.display_name}</option>)}
              </select>
            </label>
          )}
          {form.channel === "telegram" && (
            <label className="toggle wide">
              <input type="checkbox" checked={form.escalate} onChange={(e) => setForm({ ...form, escalate: e.target.checked })} />
              <span>Phone me if I don't tap Done within 10 minutes</span>
            </label>
          )}
          <div className="form-actions wide">
            <button className="btn ghost small" type="button" onClick={() => setForm(null)}>Cancel</button>
            <button className="btn primary small" type="submit" disabled={busy || !form.text.trim()}>Set reminder</button>
          </div>
        </form>
      )}
      {rows && rows.length === 0 && !form && (
        <Empty>No open reminders. Tell {assistant} "remind me at 17:00 to call the bank".</Empty>
      )}
      <div className="list">
        {(rows || []).map((r) => (
          <div className="row" key={r.id}>
            <div>
              <div>
                ⏰ {r.text} <Tag kind={STATUS_KIND[r.status] ?? ""}>{STATUS_LABEL[r.status] || r.status}</Tag>
              </div>
              <div className="meta">
                <span className="mono">{r.due_local}</span>
                {r.recurrence ? ` · ${REPEAT[r.recurrence].toLowerCase()}` : ""}
                {` · ${r.channel === "call" ? "phone call" : "Telegram"}`}
                {r.escalate ? " · calls if ignored" : ""}
                {r.user_id !== me.id ? ` · for ${r.person_name}` : ""}
              </div>
              {r.error && <div className="fail-note">{r.error}</div>}
            </div>
            {["pending", "sent"].includes(r.status) && (
              <button className="btn danger small" onClick={() => cancel(r.id)}>{r.recurrence ? "Stop" : "Cancel"}</button>
            )}
          </div>
        ))}
      </div>
    </>
  );
}
