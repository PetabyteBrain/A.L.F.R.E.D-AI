import { useEffect, useState } from "react";
import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { ErrorNote } from "./common.jsx";

/* The signed-in person's automatic briefing: when, how, weather town, what it covers (personal / business / both,
   for people with the business side), optionally a plan per weekday (daily, weekly overview or none, with a focus
   note) and skipping quiet days; plus "send one now". */

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const CONTENT = [["both", "Personal & business"], ["personal", "Personal only"], ["business", "Business only"]];
const DAY_CONTENT = [["both", "Both"], ["personal", "Personal"], ["business", "Business"]];
const SENDS = [["day", "Daily briefing"], ["week", "Week ahead"], ["off", "No briefing"]];
const NOTE_MAX = 120;

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

export default function BriefingCard({ assistant }) {
  const { data: saved, error, reload } = usePoll(() => api("/briefing"));
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState({ ok: "", err: "" });

  useEffect(() => { if (saved) setForm({ ...saved, location: saved.location || "" }); }, [saved]);
  if (!form) return <div className="briefing"><h3 className="sub">Daily briefing</h3><ErrorNote error={error} /></div>;

  const plan = form.plan;
  const business = saved.business;
  const dirty = ["enabled", "time_local", "days", "channel"].some((k) => form[k] !== saved[k])
    || form.location.trim() !== (saved.location || "") || !same(plan, saved.plan);
  const setPlan = (p) => setForm({ ...form, plan: { ...plan, ...p } });
  const setDay = (i, d) => setPlan({ days: plan.days.map((x, j) => (j === i ? { ...x, ...d } : x)) });

  async function run(fn, ok) {
    setBusy(true);
    setMsg({ ok: "", err: "" });
    try { await fn(); setMsg({ ok, err: "" }); await reload(); } catch (e) { setMsg({ ok: "", err: e.message }); } finally { setBusy(false); }
  }
  const summary = () => {
    if (!form.enabled) return "Briefing off.";
    if (plan.custom) {
      const n = plan.days.filter((d) => d.send !== "off").length;
      return `Briefing on: ${n} day${n === 1 ? "" : "s"} a week at ${form.time_local}.`;
    }
    return `Briefing on: ${form.days === "weekdays" ? "weekdays" : "every day"} at ${form.time_local}.`;
  };
  const save = (e) => {
    e.preventDefault();
    const body = { enabled: form.enabled, time_local: form.time_local, days: form.days, channel: form.channel, plan };
    if (form.location.trim() !== (saved.location || "")) body.location = form.location.trim();
    run(() => api("/briefing", { method: "PUT", body }), summary());
  };
  const sendNow = () => run(() => api("/briefing/send", { method: "POST" }),
    form.channel === "call" ? `${assistant} is calling you with the briefing.` : "Briefing sent to your Telegram.");

  return (
    <div className="briefing">
      <h3 className="sub">Daily briefing</h3>
      <p className="muted small">Each morning {assistant} sends the weather, your calendar, reminders and calls for the day,
        your open tasks and list items{business ? ", and the business side: clients, boards, the team and the company to-do" : ""}.</p>
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
          <select value={plan.custom ? "custom" : form.days}
                  onChange={(e) => (e.target.value === "custom" ? setPlan({ custom: true })
                    : setForm({ ...form, days: e.target.value, plan: { ...plan, custom: false } }))}>
            <option value="daily">Every day</option>
            <option value="weekdays">Weekdays</option>
            <option value="custom">Plan per weekday</option>
          </select>
        </label>
        {business && !plan.custom && (
          <label className="field"><span>Covers</span>
            <select value={plan.content} onChange={(e) => setPlan({ content: e.target.value })}>
              {CONTENT.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
          </label>
        )}
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

        {plan.custom && (
          <div className="briefing-plan wide" role="group" aria-label="Briefing per weekday">
            {plan.days.map((d, i) => (
              <div key={WEEKDAYS[i]} className={`briefing-day${business ? "" : " personal"}${d.send === "off" ? " off" : ""}`}>
                <b className="briefing-day-name">{WEEKDAYS[i]}</b>
                <select value={d.send} aria-label={`${WEEKDAYS[i]}: briefing`} onChange={(e) => setDay(i, { send: e.target.value })}>
                  {SENDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </select>
                {business && (
                  <select value={d.content} disabled={d.send === "off"} aria-label={`${WEEKDAYS[i]}: covers`}
                          onChange={(e) => setDay(i, { content: e.target.value })}>
                    {DAY_CONTENT.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                  </select>
                )}
                <input value={d.note} maxLength={NOTE_MAX} disabled={d.send === "off"} aria-label={`${WEEKDAYS[i]}: focus`}
                       placeholder="Focus (optional), e.g. admin, technical"
                       onChange={(e) => setDay(i, { note: e.target.value })} />
              </div>
            ))}
            <p className="muted tiny">A daily briefing covers today; "Week ahead" gives an overview of the next seven days
              (calendar{business ? ", deadlines, who's away" : ", reminders, calls"}). A focus note tells {assistant} what that
              day's briefing should concentrate on.</p>
          </div>
        )}
        <label className="toggle wide">
          <input type="checkbox" checked={plan.skip_empty} onChange={(e) => setPlan({ skip_empty: e.target.checked })} />
          <span>Skip daily briefings on days with nothing on (no events, reminders, calls or anything due){plan.custom ? "; weekly overviews are always sent" : ""}</span>
        </label>
        <div className="form-actions wide">
          <button className="btn ghost small" type="button" disabled={busy} onClick={sendNow}>Send one now</button>
          <button className="btn primary small" type="submit" disabled={busy || !dirty}>Save</button>
        </div>
      </form>
    </div>
  );
}
