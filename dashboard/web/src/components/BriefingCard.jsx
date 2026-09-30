import { useEffect, useState } from "react";
import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { ErrorNote } from "./common.jsx";

/* The signed-in person's automatic daily briefing: when, how, weather town; plus "send one now". */
export default function BriefingCard({ assistant }) {
  const { data: saved, error, reload } = usePoll(() => api("/briefing"));
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState({ ok: "", err: "" });

  useEffect(() => { if (saved) setForm({ ...saved, location: saved.location || "" }); }, [saved]);
  if (!form) return <div className="briefing"><h3 className="sub">Daily briefing</h3><ErrorNote error={error} /></div>;

  const dirty = ["enabled", "time_local", "days", "channel"].some((k) => form[k] !== saved[k])
    || form.location.trim() !== (saved.location || "");

  async function run(fn, ok) {
    setBusy(true);
    setMsg({ ok: "", err: "" });
    try { await fn(); setMsg({ ok, err: "" }); await reload(); } catch (e) { setMsg({ ok: "", err: e.message }); } finally { setBusy(false); }
  }
  const save = (e) => {
    e.preventDefault();
    const body = { enabled: form.enabled, time_local: form.time_local, days: form.days, channel: form.channel };
    if (form.location.trim() !== (saved.location || "")) body.location = form.location.trim();
    run(() => api("/briefing", { method: "PUT", body }), form.enabled ? `Briefing on: ${form.days === "weekdays" ? "weekdays" : "every day"} at ${form.time_local}.` : "Briefing off.");
  };
  const sendNow = () => run(() => api("/briefing/send", { method: "POST" }),
    form.channel === "call" ? `${assistant} is calling you with the briefing.` : "Briefing sent to your Telegram.");

  return (
    <div className="briefing">
      <h3 className="sub">Daily briefing</h3>
      <p className="muted small">Each morning {assistant} sends the weather, your calendar, reminders and calls for the day.</p>
      {msg.ok && <div className="notice" role="status">{msg.ok}</div>}
      <ErrorNote error={msg.err || error} />
      <form className="form-grid" onSubmit={save}>
        <label className="toggle wide">
          <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />
          <span>{form.enabled ? "On" : "Off"}</span>
        </label>
        <label className="field"><span>Time</span>
          <input type="time" value={form.time_local} required onChange={(e) => setForm({ ...form, time_local: e.target.value })} />
        </label>
        <label className="field"><span>Days</span>
          <select value={form.days} onChange={(e) => setForm({ ...form, days: e.target.value })}>
            <option value="daily">Every day</option>
            <option value="weekdays">Weekdays</option>
          </select>
        </label>
        <label className="field"><span>Delivery</span>
          <select value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value })}>
            <option value="telegram">Telegram message</option>
            <option value="call">Phone call</option>
          </select>
        </label>
        <label className="field"><span>Weather for</span>
          <input value={form.location} maxLength={80} placeholder="Town, e.g. Lucerne"
                 onChange={(e) => setForm({ ...form, location: e.target.value })} />
        </label>
        <div className="form-actions wide">
          <button className="btn ghost small" type="button" disabled={busy} onClick={sendNow}>Send one now</button>
          <button className="btn primary small" type="submit" disabled={busy || !dirty}>Save</button>
        </div>
      </form>
    </div>
  );
}
