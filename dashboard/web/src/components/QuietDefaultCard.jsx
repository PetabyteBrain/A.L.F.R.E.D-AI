import { useEffect, useState } from "react";
import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { ErrorNote, Tag } from "./common.jsx";

/* Admin (Settings tab): the household default quiet hours, for everyone who follows the default. */
export default function QuietDefaultCard({ assistant }) {
  const { data: q, error, reload } = usePoll(() => api("/quiet-hours"));
  const [def, setDef] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState({ ok: "", err: "" });
  useEffect(() => { if (q) setDef(q.default); }, [q]);
  if (!q || !def) return <ErrorNote error={error} />;
  const dirty = def.enabled !== q.default.enabled || def.start !== q.default.start || def.end !== q.default.end;

  async function save(e) {
    e.preventDefault();
    setBusy(true); setMsg({ ok: "", err: "" });
    try {
      await api("/quiet-hours/default", { method: "PUT", body: def });
      setMsg({ ok: "Saved for everyone who follows the household default.", err: "" });
      await reload();
    } catch (x) { setMsg({ ok: "", err: x.message }); } finally { setBusy(false); }
  }
  return (
    <section className="settings-block">
      <h3 className="sub">Household quiet hours {q.default.enabled ? <Tag>{q.default.start}–{q.default.end}</Tag> : <Tag>off</Tag>}</h3>
      <p className="muted small">
        The default for everyone who hasn't set their own: calls other people ask {assistant} to make wait until
        it ends, and automatic messages arrive silently. Each person can still choose their own hours on their profile.
      </p>
      {msg.ok && <div className="notice" role="status">{msg.ok}</div>}
      <ErrorNote error={msg.err} />
      <form className="form-grid" onSubmit={save}>
        <label className="toggle wide">
          <input type="checkbox" checked={def.enabled} onChange={(e) => setDef({ ...def, enabled: e.target.checked })} />
          <span>Household default {def.enabled ? "on" : "off"}</span>
        </label>
        <label className="field"><span>From</span>
          <input type="time" value={def.start} required onChange={(e) => setDef({ ...def, start: e.target.value })} />
        </label>
        <label className="field"><span>Until</span>
          <input type="time" value={def.end} required onChange={(e) => setDef({ ...def, end: e.target.value })} />
        </label>
        <div className="form-actions wide">
          <button className="btn primary small" type="submit" disabled={busy || !dirty}>Save default</button>
        </div>
      </form>
    </section>
  );
}
