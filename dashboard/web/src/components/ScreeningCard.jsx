import { useState } from "react";
import { api } from "../api.js";
import { fmtTime } from "../format.js";
import { usePoll } from "../hooks.js";
import { ErrorNote, Tag } from "./common.jsx";

/* Admin (People tab): unknown callers Alfred took a message from, and blocked numbers. */
export default function ScreeningCard({ assistant }) {
  const { data: s, error, reload } = usePoll(() => api("/screening"), 30000);
  const [number, setNumber] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  if (!s) return <ErrorNote error={error} />;

  async function run(path, num) {
    setBusy(true);
    setErr("");
    try { await api(`/screening/${path}`, { method: "POST", body: { number: num } }); await reload(); setNumber(""); }
    catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  const isBlocked = (n) => s.blocked.some((b) => b.number === n);

  return (
    <section className="screening">
      <h3 className="sub">Call screening {s.enabled ? <Tag kind="ok">on</Tag> : <Tag>off</Tag>}</h3>
      <p className="muted small">
        {s.enabled
          ? `Unknown numbers reach ${assistant}, who takes a message without naming anyone and relays it on Telegram: to the admin or cofounder it's for (plus admins), to whoever ${assistant} called if it's a call-back, otherwise to all admins and cofounders. Withheld and blocked numbers get "busy"; at most 3 calls per number and 20 in total a day.`
          : "Unknown numbers get \"busy\" (CALL_SCREENING=off)."}
      </p>
      <ErrorNote error={err || error} />
      {s.recent.length > 0 && (
        <div className="list">
          {s.recent.map((c) => (
            <div className="row" key={c.id}>
              <div>
                <div>{c.caller} {isBlocked(c.caller) && <Tag kind="bad">blocked</Tag>}</div>
                <div className="meta">{fmtTime(c.ts)} · {c.summary || "no message"}</div>
              </div>
              {!isBlocked(c.caller) && (
                <div className="row-actions">
                  <button className="btn danger small" disabled={busy} onClick={() => run("block", c.caller)}>Block</button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      <h3 className="sub">Blocked numbers</h3>
      {s.blocked.length === 0 && <p className="muted small">None.</p>}
      {s.blocked.map((b) => (
        <div className="kv" key={b.number}>
          <span>{b.number} <span className="muted tiny">· {fmtTime(b.at)} by {b.by}</span></span>
          <button className="btn small" disabled={busy} onClick={() => run("unblock", b.number)}>Unblock</button>
        </div>
      ))}
      <form className="field-row" onSubmit={(e) => { e.preventDefault(); run("block", number); }}>
        <label className="field"><span>Block a number</span>
          <input value={number} onChange={(e) => setNumber(e.target.value)} placeholder="+41 79 000 01 00" maxLength={30} required />
        </label>
        <button className="btn small" type="submit" disabled={busy}>Block</button>
      </form>
    </section>
  );
}
