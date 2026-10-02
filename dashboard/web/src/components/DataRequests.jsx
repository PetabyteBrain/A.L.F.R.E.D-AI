import { useState } from "react";
import { api } from "../api.js";
import { fmtTime } from "../format.js";
import { usePoll } from "../hooks.js";
import { ErrorNote, Tag } from "./common.jsx";
import { ConfirmDialog } from "./hud.jsx";

/* Admin: pending "delete my data" requests (People tab). */
export function DataRequests({ onChanged }) {
  const { data: reqs, error, reload } = usePoll(() => api("/data-requests"), 30000);
  const [confirm, setConfirm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  if (!reqs?.length && !error) return null;

  async function decide(r, approve) {
    setBusy(true);
    setErr("");
    try {
      await api(`/data-requests/${Number(r.id)}`, { method: "POST", body: { approve } });
      setConfirm(null);
      await reload();
      onChanged?.();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  }

  return (
    <section className="data-requests">
      <h3 className="sub flush">Data deletion requests</h3>
      <ErrorNote error={err || error} />
      {(reqs || []).map((r) => (
        <div className="row" key={r.id}>
          <div>
            <div>{r.display_name} <Tag>{r.role}</Tag> <Tag kind="bad">{r.scope === "account" ? "whole account" : "history"}</Tag></div>
            <div className="meta">{fmtTime(r.created_at)}{r.note ? ` · “${r.note}”` : ""}</div>
          </div>
          <div className="row-actions">
            <a className="btn small" href={`/api/people/${Number(r.user_id)}/export`} download>Export first</a>
            <button className="btn danger small" disabled={busy} onClick={() => setConfirm(r)}>Delete</button>
            <button className="btn small" disabled={busy} onClick={() => decide(r, false)}>Reject</button>
          </div>
        </div>
      ))}
      {confirm && (
        <ConfirmDialog
          title="Delete data"
          body={<>Permanently delete {confirm.scope === "account" ? <>the <b>whole account</b></> : <>the <b>history</b></>} of{" "}
            <b>{confirm.display_name}</b>: conversations, recordings, notes, reminders, tasks, meetings and settings. This can't be undone.</>}
          confirmLabel="Delete"
          busy={busy}
          error={err}
          onConfirm={() => decide(confirm, true)}
          onCancel={() => setConfirm(null)}
        />
      )}
    </section>
  );
}

/* Admin, in the person editor: export or delete a person's data directly (e.g. a request by email). */
export function PersonData({ person, isSelf, onDone }) {
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState({ ok: "", err: "" });

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setMsg({ ok: "", err: "" });
    try {
      const r = await api(`/people/${Number(person.id)}/erase`, { method: "POST", body: form });
      setForm(null);
      setMsg({ ok: `Deleted: ${r.summary.conversations} conversations, ${r.summary.memories} notes, ${r.summary.reminders} reminders.`, err: "" });
      onDone?.(form.scope);
    } catch (e2) { setMsg({ ok: "", err: e2.message }); } finally { setBusy(false); }
  }

  return (
    <>
      <h3 className="sub">Data (privacy requests)</h3>
      {msg.ok && <div className="notice" role="status">{msg.ok}</div>}
      <ErrorNote error={msg.err} />
      <div className="form-actions">
        <a className="btn small" href={`/api/people/${Number(person.id)}/export`} download>Export data (.zip)</a>
        {!form && <button className="btn danger small" disabled={busy} onClick={() => setForm({ scope: "history", password: "" })}>Delete data…</button>}
      </div>
      {form && (
        <form className="stack" onSubmit={submit}>
          <label className="field"><span>What to delete</span>
            <select value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value })}>
              <option value="history">History (keeps the person and their connections)</option>
              {!isSelf && <option value="account">Whole account</option>}
            </select>
          </label>
          <label className="field"><span>Your password</span>
            <input type="password" autoComplete="current-password" value={form.password} maxLength={200} required
                   onChange={(e) => setForm({ ...form, password: e.target.value })} />
          </label>
          <div className="form-actions">
            <button className="btn danger small" type="submit" disabled={busy}>Delete permanently</button>
            <button className="btn small" type="button" disabled={busy} onClick={() => setForm(null)}>Cancel</button>
          </div>
        </form>
      )}
    </>
  );
}
