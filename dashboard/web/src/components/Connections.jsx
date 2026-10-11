import { useEffect, useState } from "react";
import { api } from "../api.js";
import { Tag } from "./common.jsx";

export const CONNECTION_TYPES = [
  { id: "phone", label: "Phone", placeholder: "079 000 01 00 or +41 79 000 01 00", hint: "Calls from this number reach Alfred once it's verified with a code." },
  { id: "email", label: "Email", placeholder: "name@example.com", hint: "Add every address you write from, so Alfred recognises your mail. Each is verified with a code sent to it; Alfred writes to the primary one." },
  { id: "telegram", label: "Telegram", placeholder: "Numeric ID, e.g. 123456789", hint: "Get it from @userinfobot, not the @username. Verified with a code sent from that account." },
];
const label = (t) => CONNECTION_TYPES.find((c) => c.id === t)?.label || t;

/* Code flow for one unverified phone/Telegram/email connection. */
function Verify({ c, base, canOverride, onChanged }) {
  const [state, setState] = useState(null); // start() response
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  // Telegram is confirmed from the phone, not here: watch for it to flip to verified.
  useEffect(() => {
    if (state?.method !== "telegram") return undefined;
    const t = setInterval(onChanged, 4000);
    return () => clearInterval(t);
  }, [state, onChanged]);

  async function go(fn) {
    setBusy(true);
    setErr("");
    try { return await fn(); } catch (e) { setErr(e.message); return null; } finally { setBusy(false); }
  }
  const start = () => go(async () => { setState(await api(`${base}/${Number(c.id)}/verify`, { method: "POST" })); setCode(""); });
  const check = (e) => {
    e.preventDefault();
    go(async () => { await api(`${base}/${Number(c.id)}/verify/check`, { method: "POST", body: { code } }); onChanged(); });
  };
  const override = () => go(async () => { await api(`/connections/${Number(c.id)}/mark-verified`, { method: "POST" }); onChanged(); });

  return (
    <div className="verify">
      {!state && (
        <div className="verify-actions">
          <span className="muted small">
            {{ phone: "Not active until confirmed: Alfred calls this number and reads out a code.",
               email: "Not active until confirmed: Alfred emails a code to this address." }[c.type]
              || "Not active until confirmed: send a code to the bot from this Telegram account."}
          </span>
          <button className="btn primary small" type="button" disabled={busy} onClick={start}>
            {{ phone: "Call with code", email: "Email a code" }[c.type] || "Get code"}
          </button>
          {canOverride && <button className="btn ghost small" type="button" disabled={busy} onClick={override}
                                  title="Skip the code (you vouch for this connection)">Mark verified</button>}
        </div>
      )}
      {(state?.method === "call" || state?.method === "email") && (
        <form className="verify-code" onSubmit={check}>
          <span className="muted small">
            {state.method === "call" ? `Calling ${c.value} now. Enter the 6-digit code you hear:`
                                     : `Code sent to ${c.value} (check spam too). Enter the 6-digit code:`}
          </span>
          <input value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                 inputMode="numeric" autoComplete="one-time-code" placeholder="••••••" aria-label="Verification code" autoFocus />
          <button className="btn primary small" type="submit" disabled={busy || code.length !== 6}>Confirm</button>
          <button className="btn ghost small" type="button" disabled={busy} onClick={start}>
            {state.method === "call" ? "Call again" : "Send again"}
          </button>
        </form>
      )}
      {state?.method === "telegram" && (
        <div className="verify-code">
          <span className="muted small">
            From Telegram account {c.value}, send this code to {state.bot ? <b>@{state.bot}</b> : "the bot"}:
          </span>
          <code className="big-code">{state.code}</code>
          {state.link && <a className="btn primary small" href={state.link} target="_blank" rel="noopener noreferrer">Open in Telegram</a>}
          <span className="muted tiny">Valid for 10 minutes · this updates by itself once it arrives</span>
        </div>
      )}
      {err && <div className="fail-note">{err}</div>}
    </div>
  );
}

function Row({ c, busy, base, canOverride, onUpdate, onRemove, onChanged }) {
  const [value, setValue] = useState(c.value);
  const [err, setErr] = useState("");
  useEffect(() => setValue(c.value), [c.value]);
  const dirty = value.trim() !== c.value;
  async function makePrimary() {
    setErr("");
    try { await api(`${base}/${Number(c.id)}/primary`, { method: "POST" }); onChanged(); } catch (e) { setErr(e.message); }
  }
  return (
    <div className="conn-item">
      <form className="conn-row" onSubmit={(e) => { e.preventDefault(); if (dirty) onUpdate(c.id, value); }}>
        <span className="conn-type">
          {label(c.type)}
          {c.needs_verification && (c.verified ? <Tag kind="ok">Verified</Tag> : <Tag kind="busy">Unverified</Tag>)}
          {c.primary && <Tag kind="ok">Primary</Tag>}
        </span>
        <input value={value} onChange={(e) => setValue(e.target.value)} maxLength={254} aria-label={`${label(c.type)} value`} />
        <button className="btn small" type="submit" disabled={busy || !dirty}>Save</button>
        {c.type === "email" && c.verified && c.primary === false && !dirty &&
          <button className="btn ghost small" type="button" disabled={busy} onClick={makePrimary}
                  title="Alfred writes to this address">Make primary</button>}
        <button className="btn danger small" type="button" disabled={busy} onClick={() => onRemove(c)}>Remove</button>
      </form>
      {err && <div className="fail-note">{err}</div>}
      {c.needs_verification && !c.verified && !dirty &&
        <Verify key={c.value} c={c} base={base} canOverride={canOverride} onChanged={onChanged} />}
    </div>
  );
}

/* Editable list of phone/email/Telegram connections. Parent does the add/edit/remove calls;
   verification talks to `${base}/{id}/verify…` directly and calls onChanged when done. */
export default function Connections({ connections, busy, base, canOverride = false, onAdd, onUpdate, onRemove, onChanged }) {
  const [type, setType] = useState("phone");
  const [value, setValue] = useState("");
  const meta = CONNECTION_TYPES.find((c) => c.id === type);

  async function add(e) {
    e.preventDefault();
    if (await onAdd(type, value)) setValue("");
  }

  return (
    <div className="connections">
      {connections.length === 0 && <div className="muted small">No connections yet.</div>}
      {connections.map((c) => (
        <Row key={c.id} c={c} busy={busy} base={base} canOverride={canOverride}
             onUpdate={onUpdate} onRemove={onRemove} onChanged={onChanged} />
      ))}
      <form className="conn-row add" onSubmit={add}>
        <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Connection type">
          {CONNECTION_TYPES.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
        </select>
        <input value={value} onChange={(e) => setValue(e.target.value)} placeholder={meta.placeholder}
               maxLength={254} required aria-label="New connection value" />
        <button className="btn primary small" type="submit" disabled={busy || !value.trim()}>Add</button>
      </form>
      {meta.hint && <div className="muted tiny hint">{meta.hint}</div>}
    </div>
  );
}
