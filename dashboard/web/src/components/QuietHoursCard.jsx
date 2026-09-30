import { useEffect, useState } from "react";
import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { ErrorNote, Tag } from "./common.jsx";

/* Own quiet hours (follow the default / own window / off); admins also set the household default. */
export default function QuietHoursCard({ assistant, isAdmin }) {
  const { data: q, error, reload } = usePoll(() => api("/quiet-hours"), 60000);
  const [own, setOwn] = useState(null);
  const [def, setDef] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState({ ok: "", err: "" });

  useEffect(() => {
    if (!q) return;
    setOwn({ mode: q.mode, start: q.start || q.default.start, end: q.end || q.default.end });
    setDef(q.default);
  }, [q]);
  if (!q || !own || !def) return <div className="quiet"><h3 className="sub">Quiet hours</h3><ErrorNote error={error} /></div>;

  async function run(fn, ok) {
    setBusy(true);
    setMsg({ ok: "", err: "" });
    try { await fn(); setMsg({ ok, err: "" }); await reload(); } catch (e) { setMsg({ ok: "", err: e.message }); } finally { setBusy(false); }
  }
  const defLabel = def.enabled ? `${q.default.start}–${q.default.end}` : "off";
  const ownDirty = own.mode !== q.mode || (own.mode === "custom" && (own.start !== q.start || own.end !== q.end));
  const defDirty = def.enabled !== q.default.enabled || def.start !== q.default.start || def.end !== q.default.end;

  return (
    <div className="quiet">
      <h3 className="sub">
        Quiet hours {q.quiet_now ? <Tag kind="busy">quiet until {q.until_local}</Tag> : q.effective ? <Tag>{q.effective.start}–{q.effective.end}</Tag> : <Tag>off</Tag>}
      </h3>
      <p className="muted small">
        Calls other people ask {assistant} to make wait until your quiet hours end, and automatic messages arrive
        silently. Your own reminders, briefing and "call me" still come through.
      </p>
      {msg.ok && <div className="notice" role="status">{msg.ok}</div>}
      <ErrorNote error={msg.err || error} />
      <form className="form-grid" onSubmit={(e) => {
        e.preventDefault();
        run(() => api("/quiet-hours", { method: "PUT", body: own.mode === "custom" ? own : { mode: own.mode } }), "Quiet hours saved.");
      }}>
        <label className="field wide"><span>My quiet hours</span>
          <select value={own.mode} onChange={(e) => setOwn({ ...own, mode: e.target.value })}>
            <option value="default">Household default ({defLabel})</option>
            <option value="custom">My own hours</option>
            <option value="off">None: I can always be called</option>
          </select>
        </label>
        {own.mode === "custom" && (
          <>
            <label className="field"><span>From</span>
              <input type="time" value={own.start} required onChange={(e) => setOwn({ ...own, start: e.target.value })} />
            </label>
            <label className="field"><span>Until</span>
              <input type="time" value={own.end} required onChange={(e) => setOwn({ ...own, end: e.target.value })} />
            </label>
          </>
        )}
        <div className="form-actions wide">
          <button className="btn primary small" type="submit" disabled={busy || !ownDirty}>Save</button>
        </div>
      </form>

      {isAdmin && (
        <form className="form-grid quiet-default" onSubmit={(e) => {
          e.preventDefault();
          run(() => api("/quiet-hours/default", { method: "PUT", body: def }), "Default quiet hours saved for everyone who follows them.");
        }}>
          <label className="toggle wide">
            <input type="checkbox" checked={def.enabled} onChange={(e) => setDef({ ...def, enabled: e.target.checked })} />
            <span>Household default (admin): {def.enabled ? "on" : "off"}</span>
          </label>
          <label className="field"><span>From</span>
            <input type="time" value={def.start} required onChange={(e) => setDef({ ...def, start: e.target.value })} />
          </label>
          <label className="field"><span>Until</span>
            <input type="time" value={def.end} required onChange={(e) => setDef({ ...def, end: e.target.value })} />
          </label>
          <div className="form-actions wide">
            <button className="btn small" type="submit" disabled={busy || !defDirty}>Save default</button>
          </div>
        </form>
      )}
    </div>
  );
}
