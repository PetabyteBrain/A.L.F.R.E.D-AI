import { useEffect, useState } from "react";
import { api } from "../api.js";
import FilePreview, { previewKind } from "./FilePreview.jsx";
import { Tag } from "./common.jsx";

const kb = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

function urlsFor(emailId, a) {
  const base = `/api/mail/${Number(emailId)}/attachments/${Number(a.idx)}`;
  return {
    view: `${base}/view`,
    pdf: `${base}/view?as=pdf`,
    prepare: () => api(`/mail/${Number(emailId)}/attachments/${Number(a.idx)}/prepare`, { method: "POST" }),
    data: () => api(`/mail/${Number(emailId)}/attachments/${Number(a.idx)}/preview`),
    download: base,
  };
}

function PreviewDialog({ emailId, a, onClose }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const urls = urlsFor(emailId, a);
  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div className="hud modal editor mail-preview" role="dialog" aria-modal="true" aria-label={`Preview of ${a.name}`}
           onClick={(e) => e.stopPropagation()}>
        <div className="mail-preview-head">
          <b className="mail-att-name" title={a.name}>{a.name}</b>
          <span className="muted tiny">{kb(a.size)}</span>
          <a className="btn small ghost" href={urls.download} download>Download</a>
          <button className="btn small" onClick={onClose} autoFocus>Close</button>
        </div>
        <FilePreview path={a.name} urls={urls} />
      </div>
    </div>
  );
}

function SaveForm({ emailId, a, team, onSaved, onCancel }) {
  const [scope, setScope] = useState(team ? "company" : "personal");
  const [folder, setFolder] = useState("");
  const [name, setName] = useState(a.name);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  async function save(e) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      const r = await api(`/mail/${Number(emailId)}/attachments/${Number(a.idx)}/save`,
                          { method: "POST", body: { scope, folder, filename: name } });
      onSaved(`${r.scope === "company" ? "Company" : "My files"}: ${r.saved}`);
    } catch (e2) { setErr(e2.message); } finally { setBusy(false); }
  }
  return (
    <form className="mail-save" onSubmit={save}>
      <select value={scope} onChange={(e) => setScope(e.target.value)} aria-label="Where">
        <option value="personal">My files</option>
        {team && <option value="company">Company files</option>}
      </select>
      <input value={folder} onChange={(e) => setFolder(e.target.value)} placeholder="Folder, e.g. Clients/Acme" maxLength={400}
             aria-label="Folder" autoFocus />
      <input value={name} onChange={(e) => setName(e.target.value)} maxLength={120} aria-label="File name" />
      <button className="btn small primary" type="submit" disabled={busy}>Save</button>
      <button className="btn small ghost" type="button" onClick={onCancel}>Cancel</button>
      {err && <span className="fail-note">{err}</span>}
    </form>
  );
}

/* The attachments of one message: preview, download, save to Files. Risky ones: download only, with a warning. */
export default function MailAttachments({ emailId, items, team, onChanged }) {
  const [preview, setPreview] = useState(null);
  const [saving, setSaving] = useState(null);
  const [done, setDone] = useState("");
  if (!items?.length) return null;
  return (
    <div className="mail-atts">
      {items.map((a) => (
        <div key={a.idx} className={`mail-att${a.flagged ? " risky" : ""}`}>
          <span className="mail-att-name" title={a.name}>📎 {a.name}</span>
          <span className="muted tiny">{kb(a.size)}</span>
          {a.flagged && <Tag kind="bad">risky: {a.flagged}</Tag>}
          {a.saved_to && <span className="muted tiny" title={a.saved_to}>filed</span>}
          <span className="mail-att-actions">
            {!a.flagged && previewKind(a.name) && <button className="btn tiny-btn ghost" onClick={() => setPreview(a)}>Preview</button>}
            <a className="btn tiny-btn ghost" href={urlsFor(emailId, a).download} download
               onClick={(e) => { if (a.flagged && !window.confirm(`“${a.name}” looks risky (${a.flagged}). Download anyway?`)) e.preventDefault(); }}>
              Download</a>
            {!a.flagged && <button className="btn tiny-btn ghost" onClick={() => { setSaving(a.idx); setDone(""); }}>Save to Files</button>}
          </span>
          {saving === a.idx && (
            <SaveForm emailId={emailId} a={a} team={team} onCancel={() => setSaving(null)}
                      onSaved={(where) => { setSaving(null); setDone(`Saved to ${where}`); onChanged?.(); }} />
          )}
        </div>
      ))}
      {done && <div className="ok-note small">{done}</div>}
      {preview && <PreviewDialog emailId={emailId} a={preview} onClose={() => setPreview(null)} />}
    </div>
  );
}
