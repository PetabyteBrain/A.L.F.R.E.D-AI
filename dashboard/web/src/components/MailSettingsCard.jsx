import { useState } from "react";
import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { ErrorNote, Tag } from "./common.jsx";

const MODES = [
  ["answer", "Answer what public information covers", "Questions the company's public information fully answers get an answer; everything else is acknowledged and passed to the team."],
  ["acknowledge", "Acknowledge only", "Every message gets a short \"passed to the team\" reply and goes to the team."],
  ["silent", "No replies", "Nothing is sent back; the team still hears about anything that needs a person."],
];

/* Admin (Settings tab): Alfred's email address and how much it answers mail from outside. */
export default function MailSettingsCard({ assistant }) {
  const { data: s, error, reload } = usePoll(() => api("/mail/status"), 0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  if (!s) return <ErrorNote error={error} />;

  async function toggleAutofile(on) {
    setBusy(true);
    setErr("");
    try { await api("/mail/inquiry-autofile", { method: "PUT", body: { on } }); await reload(); }
    catch (e) { setErr(e.message); } finally { setBusy(false); }
  }

  async function setMode(mode) {
    setBusy(true);
    setErr("");
    try { await api("/mail/stranger-mode", { method: "PUT", body: { mode } }); await reload(); }
    catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  return (
    <section className="settings-block">
      <h3 className="sub">Email {s.enabled ? <Tag kind="ok">{s.address}</Tag> : <Tag>not set up</Tag>}
        {s.enabled && !s.receiving && <> <Tag kind="busy">receiving off</Tag></>}</h3>
      <p className="muted small">
        How {assistant} answers mail from people who aren't registered, or who write from an address that isn't verified
        on their profile. They never get tools or anyone's data, and only senders whose address passed the mail
        provider's check get a reply.
      </p>
      <div className="mode-list" role="radiogroup" aria-label="Replies to outside email">
        {MODES.map(([id, label, hint]) => (
          <label key={id} className={`mode-opt${s.stranger_mode === id ? " on" : ""}`}>
            <input type="radio" name="stranger-mode" checked={s.stranger_mode === id} disabled={busy}
                   onChange={() => setMode(id)} />
            <span><b>{label}</b><span className="muted tiny">{hint}</span></span>
          </label>
        ))}
      </div>
      {s.retention_days && (
        <p className="muted tiny">
          Kept: personal mail {s.retention_days.personal ? `${Math.round(s.retention_days.personal / 365 * 10) / 10} years` : "forever"},
          company mail {s.retention_days.company ? `${Math.round(s.retention_days.company / 365 * 10) / 10} years` : "forever"},
          spam and automated mail 30 days (MAIL_RETENTION_DAYS, MAIL_COMPANY_RETENTION_DAYS).
        </p>
      )}
      <label className="mode-opt">
        <input type="checkbox" checked={!!s.inquiry_autofile} disabled={busy}
               onChange={(e) => toggleAutofile(e.target.checked)} />
        <span><b>File inquiry attachments automatically</b><span className="muted tiny">Safe attachments of outside
          inquiries go to the company files under Mail/Attachments/&lt;year&gt;/&lt;sender&gt;. Off: they stay with the
          email on the Mail tab, one click from “Save to Files”.</span></span>
      </label>
      <ErrorNote error={err} />
    </section>
  );
}
