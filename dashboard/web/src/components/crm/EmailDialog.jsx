import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { ErrorNote } from "../common.jsx";
import { Icon } from "../icons.jsx";

/* A follow-up email to a client (CRM stage 5). Alfred writes a draft from the record and its timeline, the person
   edits it, and sending waits for someone with approval rights like everything else that goes to clients.
   For deals the suggested next step can be saved on the deal. */

export default function EmailDialog({ entity, record, assistant = "Alfred", onClose, onSent, onNextStep }) {
  const [to, setTo] = useState("");
  const [options, setOptions] = useState([]);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [hint, setHint] = useState("");
  const [nextStep, setNextStep] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    api(`/crm-actions/recipients?entity=${entity}&id=${record.id}`)
      .then((r) => { setOptions(r); if (r[0]) setTo(r[0].email); }).catch(() => {});
  }, [entity, record.id]);
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && !busy && !drafting) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, drafting, onClose]);

  async function draft() {
    setDrafting(true); setErr("");
    try {
      const d = await api("/crm-actions/email-draft", { method: "POST", body: { entity, id: record.id, hint } });
      setSubject(d.subject); setBody(d.body); setNextStep(d.next_step || "");
      if (!to && d.to?.[0]) setTo(d.to[0].email);
    } catch (e) { setErr(e.message); } finally { setDrafting(false); }
  }
  async function send(e) {
    e.preventDefault();
    setBusy(true); setErr("");
    try { onSent(await api("/crm-actions/email", { method: "POST", body: { entity, id: record.id, to, subject, body } })); }
    catch (e2) { setErr(e2.message); } finally { setBusy(false); }
  }
  const listId = `crm-email-to-${record.id}`;
  return (
    <div className="modal-backdrop" role="presentation" onClick={() => !busy && !drafting && onClose()}>
      <form className="hud modal editor crm-editor crm-email" role="dialog" aria-modal="true" aria-label="Email the client"
            onClick={(e) => e.stopPropagation()} onSubmit={send}>
        <header className="hud-head">
          <h2><span className="hud-code">@</span>Email about {record.name}</h2>
          <button type="button" className="btn ghost small" onClick={onClose} disabled={busy}>Close</button>
        </header>
        <div className="crm-email-draft">
          <input value={hint} maxLength={500} onChange={(e) => setHint(e.target.value)} aria-label="What should the email say?"
                 placeholder="Optional: what should it say? e.g. “ask for a call next week about the offer”" />
          <button type="button" className="btn small" onClick={draft} disabled={drafting}>
            <Icon name="note" className="btn-ico" />{drafting ? "Writing…" : body ? "Write again" : `Let ${assistant} draft it`}</button>
        </div>
        <ErrorNote error={err} />
        <div className="form-grid">
          <label className="field wide"><span>To</span>
            <input type="email" required value={to} maxLength={254} list={listId} onChange={(e) => setTo(e.target.value)}
                   placeholder={options.length ? "" : "No address on record: type one"} />
            <datalist id={listId}>{options.map((o) => <option key={o.email} value={o.email}>{o.name}</option>)}</datalist>
          </label>
          <label className="field wide"><span>Subject</span>
            <input required value={subject} maxLength={200} onChange={(e) => setSubject(e.target.value)} />
          </label>
          <label className="field wide"><span>Text</span>
            <textarea required rows={10} value={body} maxLength={5000} onChange={(e) => setBody(e.target.value)}
                      placeholder="Write it yourself, or let the draft fill it in." />
          </label>
        </div>
        {entity === "deal" && nextStep && onNextStep && (
          <div className="crm-callout">
            <span className="crm-callout-label">Suggested next step</span>
            <b>{nextStep}</b>
            <button type="button" className="btn small" onClick={() => { onNextStep(nextStep); setNextStep(""); }}>Save on the deal</button>
          </div>
        )}
        <p className="muted tiny">It goes out from the company mailbox in the company layout, signed on your behalf, once someone
          with approval rights approves it (you can approve your own on Telegram or under Approvals).</p>
        <div className="modal-actions">
          <button type="button" className="btn ghost small" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn primary small" disabled={busy || !to || !subject || !body}>
            <Icon name="send" className="btn-ico" />Send for approval</button>
        </div>
      </form>
    </div>
  );
}
