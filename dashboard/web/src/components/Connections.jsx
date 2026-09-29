import { useEffect, useState } from "react";

export const CONNECTION_TYPES = [
  { id: "phone", label: "Phone", placeholder: "079 000 01 00 or +41 79 000 01 00", hint: "Calls from this number reach Alfred." },
  { id: "email", label: "Email", placeholder: "name@example.com", hint: "" },
  { id: "telegram", label: "Telegram", placeholder: "Numeric ID, e.g. 123456789", hint: "Get it from @userinfobot, not the @username." },
];
const label = (t) => CONNECTION_TYPES.find((c) => c.id === t)?.label || t;

function Row({ c, busy, onUpdate, onRemove }) {
  const [value, setValue] = useState(c.value);
  useEffect(() => setValue(c.value), [c.value]);
  const dirty = value.trim() !== c.value;
  return (
    <form className="conn-row" onSubmit={(e) => { e.preventDefault(); if (dirty) onUpdate(c.id, value); }}>
      <span className="conn-type">{label(c.type)}</span>
      <input value={value} onChange={(e) => setValue(e.target.value)} maxLength={254} aria-label={`${label(c.type)} value`} />
      <button className="btn small" type="submit" disabled={busy || !dirty}>Save</button>
      <button className="btn danger small" type="button" disabled={busy} onClick={() => onRemove(c)}>Remove</button>
    </form>
  );
}

/* Editable list of phone/email/Telegram connections. Parent does the API calls. */
export default function Connections({ connections, busy, onAdd, onUpdate, onRemove }) {
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
      {connections.map((c) => <Row key={c.id} c={c} busy={busy} onUpdate={onUpdate} onRemove={onRemove} />)}
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
