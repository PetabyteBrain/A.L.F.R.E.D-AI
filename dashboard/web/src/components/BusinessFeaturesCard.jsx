import { useState } from "react";
import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { ErrorNote, Tag } from "./common.jsx";

/* Settings: switch the business side (CRM and pipeline, quotes and invoices, boards and teams, employees) on or off
   for everyone. Only admins can change it; others with settings.manage see the state. */
export default function BusinessFeaturesCard({ assistant, onChanged }) {
  const { data: f, error, reload } = usePoll(() => api("/settings/features"));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  if (!f) return <ErrorNote error={error} />;

  async function toggle() {
    const on = !f.business;
    if (!on && !window.confirm(`Turn off the CRM, pipeline, boards and team features for everyone? ${assistant} keeps only `
      + "the personal-assistant side. Nothing is deleted: turning it back on brings everything back as it was.")) return;
    setBusy(true); setErr(""); setOk("");
    try {
      await api("/settings/features", { method: "PUT", body: { business: on } });
      setOk(on ? "Business features are on again." : `Business features are off: ${assistant} is a personal assistant only.`);
      await reload();
      onChanged?.();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }

  return (
    <section>
      <h3 className="sub">Business features {f.business ? <Tag kind="ok">on</Tag> : <Tag>off</Tag>}</h3>
      <p className="muted small">The CRM and sales pipeline, quotes and invoices, work boards and teams, and the employee
        directory with absences and checklists. Turned off, those tabs, {assistant}'s tools for them, their parts of the
        briefing and the proactive reminders and reports are gone for everyone, and only the personal-assistant side stays
        (chat, calls, calendar, reminders, tasks, notes, mail, files). Roles keep their settings and no data is deleted.</p>
      {ok && <div className="notice" role="status">{ok}</div>}
      <ErrorNote error={err || error} />
      {f.can_change ? (
        <div className="form-actions">
          <button className={`btn small${f.business ? " danger" : " primary"}`} disabled={busy} onClick={toggle}>
            {f.business ? "Turn business features off" : "Turn business features on"}</button>
        </div>
      ) : <p className="muted tiny">Only an admin can switch this.</p>}
    </section>
  );
}
