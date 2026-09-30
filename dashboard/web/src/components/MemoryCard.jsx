import { useState } from "react";
import { api } from "../api.js";
import { usePoll } from "../hooks.js";
import { ErrorNote } from "./common.jsx";

function Note({ n, busy, onSave, onDelete }) {
  const [text, setText] = useState(n.text);
  const dirty = text.trim() !== n.text;
  return (
    <form className="memo-row" onSubmit={(e) => { e.preventDefault(); if (dirty) onSave(n.id, text); }}>
      <input value={text} maxLength={500} onChange={(e) => setText(e.target.value)} aria-label="Note" />
      {dirty && <button className="btn small" type="submit" disabled={busy}>Save</button>}
      <button className="btn danger small" type="button" disabled={busy} onClick={() => onDelete(n.id)} aria-label="Forget this note">Forget</button>
    </form>
  );
}

/* What Alfred remembers about the signed-in person (their long-term memory), editable. */
export default function MemoryCard({ assistant }) {
  const { data: notes, error, reload } = usePoll(() => api("/memory"));
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function run(fn) {
    setBusy(true);
    setErr("");
    try { await fn(); await reload(); return true; } catch (e) { setErr(e.message); return false; } finally { setBusy(false); }
  }

  return (
    <div className="memory">
      <h3 className="sub">{assistant}'s notes about you</h3>
      <p className="muted small">
        Kept across Telegram, calls and chat, and only used when {assistant} talks to you. Say "remember that…" to add one.
      </p>
      <ErrorNote error={err || error} />
      {notes && notes.length === 0 && <p className="muted small">Nothing noted yet.</p>}
      <div className="memo-list">
        {(notes || []).map((n) => (
          <Note key={`${n.id}-${n.updated_at}`} n={n} busy={busy}
                onSave={(id, t) => run(() => api(`/memory/${Number(id)}`, { method: "PATCH", body: { text: t } }))}
                onDelete={(id) => run(() => api(`/memory/${Number(id)}`, { method: "DELETE" }))} />
        ))}
      </div>
      <form className="memo-row add" onSubmit={async (e) => {
        e.preventDefault();
        if (await run(() => api("/memory", { method: "POST", body: { text } }))) setText("");
      }}>
        <input value={text} maxLength={500} placeholder="e.g. Prefers calls after 9:00" onChange={(e) => setText(e.target.value)}
               aria-label="New note" />
        <button className="btn primary small" type="submit" disabled={busy || !text.trim()}>Add</button>
      </form>
    </div>
  );
}
