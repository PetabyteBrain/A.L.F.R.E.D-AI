import { useState } from "react";
import { api } from "../api.js";
import { fmtTime } from "../format.js";
import { usePoll } from "../hooks.js";
import { ErrorNote, Tag } from "./common.jsx";

/* Download everything stored about me; ask an admin to delete my history or account. */
export default function MyDataCard({ assistant }) {
  const { data, error, reload } = usePoll(() => api("/profile/data/request"), 60000);
  const [form, setForm] = useState(null);       // {scope, note, password} while asking
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const pending = data?.pending;
  const retentionDays = data?.retention_days || 0;

  async function run(fn) {
    setBusy(true);
    setErr("");
    try { await fn(); await reload(); return true; } catch (e) { setErr(e.message); return false; } finally { setBusy(false); }
  }

  return (
    <div className="my-data">
      <h3 className="sub">Your data</h3>
      <p className="muted small">
        Download a copy of everything {assistant} stores about you: conversations and transcripts, call recordings,
        notes, reminders, tasks, meetings and settings.
        {retentionDays > 0 && ` Call audio is kept for ${retentionDays} days, transcripts until you delete them.`}
      </p>
      <ErrorNote error={err || error} />
      <a className="btn small" href="/api/profile/data/export" download>Download my data (.zip)</a>

      {pending ? (
        <div className="notice-box">
          <p className="small">
            <Tag kind="busy">pending</Tag> You asked on {fmtTime(pending.created_at)} to delete your{" "}
            {pending.scope === "account" ? "whole account" : "history"}. An admin will confirm it.
          </p>
          <button className="btn small" disabled={busy} onClick={() => run(() => api("/profile/data/request", { method: "DELETE" }))}>Withdraw request</button>
        </div>
      ) : !form ? (
        <button className="btn danger small" disabled={busy} onClick={() => setForm({ scope: "history", note: "", password: "" })}>Delete my data…</button>
      ) : (
        <form className="stack" onSubmit={async (e) => {
          e.preventDefault();
          if (await run(() => api("/profile/data/request", { method: "POST", body: form }))) setForm(null);
        }}>
          <label className="field"><span>What to delete</span>
            <select value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value })}>
              <option value="history">My history: conversations, recordings, notes, reminders, tasks, meetings, settings</option>
              <option value="account">My whole account: all of that, and {assistant} no longer knows me</option>
            </select>
          </label>
          <label className="field"><span>Note for the admin (optional)</span>
            <input value={form.note} maxLength={300} onChange={(e) => setForm({ ...form, note: e.target.value })} />
          </label>
          <label className="field"><span>Confirm with your password</span>
            <input type="password" autoComplete="current-password" value={form.password} maxLength={200} required
                   onChange={(e) => setForm({ ...form, password: e.target.value })} />
          </label>
          <p className="muted tiny">An admin confirms the deletion. It can't be undone. Copies in the backups expire within the retention period.</p>
          <div className="form-actions">
            <button className="btn danger small" type="submit" disabled={busy}>Ask for deletion</button>
            <button className="btn small" type="button" disabled={busy} onClick={() => setForm(null)}>Cancel</button>
          </div>
        </form>
      )}
    </div>
  );
}
