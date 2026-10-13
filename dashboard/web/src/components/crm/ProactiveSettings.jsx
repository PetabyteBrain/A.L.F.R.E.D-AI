import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { ErrorNote } from "../common.jsx";

/* When Alfred nudges people about follow-ups, sends the weekly CRM report, and logs meetings (crm.settings). */

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export default function ProactiveSettings({ onClose, onChanged }) {
  const [s, setS] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  useEffect(() => { api("/crm-actions/proactive-settings").then(setS).catch((e) => setErr(e.message)); }, []);
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);
  const set = (k, v) => setS((x) => ({ ...x, [k]: v }));
  async function save(e) {
    e.preventDefault();
    setBusy(true); setErr("");
    try { await api("/crm-actions/proactive-settings", { method: "PUT", body: { settings: { ...s, stale_days: Number(s.stale_days) } } }); onChanged?.(); onClose(); }
    catch (e2) { setErr(e2.message); } finally { setBusy(false); }
  }
  return (
    <div className="modal-backdrop" role="presentation" onClick={() => !busy && onClose()}>
      <form className="hud modal editor" role="dialog" aria-modal="true" aria-label="Follow-ups and reports"
            onClick={(e) => e.stopPropagation()} onSubmit={save}>
        <header className="hud-head">
          <h2><span className="hud-code">FU</span>Follow-ups &amp; reports</h2>
          <button type="button" className="btn ghost small" onClick={onClose} disabled={busy}>Close</button>
        </header>
        <ErrorNote error={err} />
        {!s ? <p className="muted">Loading…</p> : (
          <div className="form-grid">
            <label className="field"><span>A lead needs a nudge after (days without contact)</span>
              <input type="number" min={3} max={180} value={s.stale_days} onChange={(e) => set("stale_days", e.target.value)} />
            </label>
            <div className="field" />
            <label className="toggle wide"><input type="checkbox" checked={s.nudges} onChange={(e) => set("nudges", e.target.checked)} />
              <span>Send everyone their follow-ups on weekday mornings (in the briefing if they get one, otherwise on Telegram)</span></label>
            <label className="field"><span>At</span>
              <input type="time" value={s.nudge_time} disabled={!s.nudges} onChange={(e) => set("nudge_time", e.target.value)} />
            </label>
            <div className="field" />
            <label className="toggle wide"><input type="checkbox" checked={s.report} onChange={(e) => set("report", e.target.checked)} />
              <span>Weekly CRM report on Telegram to everyone allowed to see reports (covers the week before)</span></label>
            <label className="field"><span>Day</span>
              <select value={s.report_day} disabled={!s.report} onChange={(e) => set("report_day", Number(e.target.value))}>
                {DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
              </select>
            </label>
            <label className="field"><span>At</span>
              <input type="time" value={s.report_time} disabled={!s.report} onChange={(e) => set("report_time", e.target.value)} />
            </label>
            <label className="toggle wide"><input type="checkbox" checked={s.auto_meetings} onChange={(e) => set("auto_meetings", e.target.checked)} />
              <span>Log meetings from linked Google Calendars on the client's timeline when a CRM contact was invited</span></label>
            <label className="toggle wide"><input type="checkbox" checked={s.checklist_reminders} onChange={(e) => set("checklist_reminders", e.target.checked)} />
              <span>Remind people of onboarding and offboarding steps on the morning they're due</span></label>
          </div>
        )}
        <div className="modal-actions">
          <button type="button" className="btn ghost small" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn primary small" disabled={busy || !s}>Save</button>
        </div>
      </form>
    </div>
  );
}
