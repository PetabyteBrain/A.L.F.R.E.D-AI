import { useEffect, useState } from "react";
import { api } from "../api.js";

/* Ask Alfred to phone someone now or at a set time, optionally with a purpose he reports back on. */
export default function CallDialog({ assistant, targets, initialTarget, onDone, onCancel }) {
  const [target, setTarget] = useState(initialTarget ? String(initialTarget.id) : "");
  const [purpose, setPurpose] = useState("");
  const [when, setWhen] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && !busy) onCancel(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onCancel]);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      const r = await api("/calls", { method: "POST", body: { user_id: Number(target), purpose, when: when || null } });
      onDone(r.request);
    } catch (e2) {
      setErr(e2.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="modal-backdrop" role="presentation" onClick={() => !busy && onCancel()}>
      <form className="hud modal" role="dialog" aria-modal="true" aria-labelledby="call-title"
            onClick={(e) => e.stopPropagation()} onSubmit={submit}>
        <h2 id="call-title"><span className="hud-code">☎</span>Have {assistant} call</h2>
        <label className="field"><span>Person</span>
          <select value={target} onChange={(e) => setTarget(e.target.value)} required>
            <option value="" disabled>Choose…</option>
            {targets.map((t) => <option key={t.id} value={t.id}>{t.display_name}</option>)}
          </select>
        </label>
        {targets.length === 0 && <p className="error">Nobody with a phone number (and phone access) is registered yet.</p>}
        <label className="field"><span>What should {assistant} ask or tell them? (optional)</span>
          <textarea value={purpose} onChange={(e) => setPurpose(e.target.value)} maxLength={600} rows={3}
                    placeholder="e.g. Ask whether he can send the invoice by Friday." />
        </label>
        <label className="field"><span>When (leave empty for now)</span>
          <input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
        </label>
        <p className="muted tiny">
          {purpose ? `${assistant} hangs up when it's handled and reports the answer back to you.` : "Without a purpose it's a plain call."}
          {" "}Unanswered calls are retried twice, 10 minutes apart.
        </p>
        {err && <p className="error">{err}</p>}
        <div className="modal-actions">
          <button className="btn ghost" type="button" onClick={onCancel} disabled={busy}>Cancel</button>
          <button className="btn primary" type="submit" disabled={busy || !target}>{busy ? "Working…" : when ? "Schedule" : "Call now"}</button>
        </div>
      </form>
    </div>
  );
}
