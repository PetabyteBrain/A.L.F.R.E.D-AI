import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { ErrorNote } from "../common.jsx";

/* New or edit a deal. A deal started from a company has that company preset; its people are offered as contact. */

const EMPTY = { title: "", company_id: "", contact_id: "", stage_id: "", value: "", probability: "", expected_close: "",
                next_step: "", next_step_at: "", source: "", notes: "", owner_id: "", group_id: "" };

export default function DealEditor({ deal, presetCompany, status, onSaved, onClose }) {
  const isNew = !deal;
  const [form, setForm] = useState(() => {
    const f = { ...EMPTY };
    if (deal) for (const k of Object.keys(f)) f[k] = deal[k] ?? "";
    if (isNew && presetCompany) f.company_id = presetCompany.id;
    if (isNew) f.stage_id = (status?.stages || []).find((s) => s.kind === "open")?.id || "";
    return f;
  });
  const [companies, setCompanies] = useState(() => {
    const c = presetCompany || (deal?.company_id ? { id: deal.company_id, name: deal.company_name } : null);
    return c ? [c] : [];
  });
  const [companyQ, setCompanyQ] = useState("");
  const [contacts, setContacts] = useState([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const stages = status?.stages || [];
  const stage = stages.find((s) => String(s.id) === String(form.stage_id));

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);
  useEffect(() => {
    if (!companyQ.trim()) return undefined;
    const t = setTimeout(() => api(`/crm/companies?limit=8&q=${encodeURIComponent(companyQ.trim())}`)
      .then((d) => setCompanies((old) => [...old.filter((c) => String(c.id) === String(form.company_id)),
        ...d.items.filter((c) => String(c.id) !== String(form.company_id))])).catch(() => {}), 250);
    return () => clearTimeout(t);
  }, [companyQ, form.company_id]);
  useEffect(() => {
    if (!form.company_id) { setContacts([]); return; }
    api(`/crm/contacts?limit=100&company_id=${Number(form.company_id)}`).then((d) => setContacts(d.items)).catch(() => setContacts([]));
  }, [form.company_id]);

  async function save(e) {
    e.preventDefault();
    setBusy(true); setErr("");
    const fields = { ...form };
    for (const k of ["company_id", "contact_id", "stage_id", "owner_id", "group_id"]) fields[k] = fields[k] ? Number(fields[k]) : null;
    fields.value = fields.value === "" ? 0 : fields.value;
    fields.probability = fields.probability === "" ? null : Number(fields.probability);
    for (const k of ["expected_close", "next_step_at"]) fields[k] = fields[k] || null;
    if (isNew) { if (!fields.owner_id) delete fields.owner_id; }
    else {
      for (const k of Object.keys(fields)) {
        const before = deal[k] ?? null;
        if (String(before ?? "") === String(fields[k] ?? "")) delete fields[k];
      }
    }
    try {
      onSaved(await api(isNew ? "/crm/deals" : `/crm/deals/${deal.id}`, { method: isNew ? "POST" : "PATCH", body: { fields } }));
    } catch (e2) { setErr(e2.message); } finally { setBusy(false); }
  }
  return (
    <div className="modal-backdrop" role="presentation" onClick={() => !busy && onClose()}>
      <form className="hud modal editor crm-editor" role="dialog" aria-modal="true" aria-label={isNew ? "New deal" : "Edit deal"}
            onClick={(e) => e.stopPropagation()} onSubmit={save}>
        <header className="hud-head">
          <h2><span className="hud-code">DL</span>{isNew ? "New deal" : "Edit deal"}</h2>
          <button type="button" className="btn ghost small" onClick={onClose} disabled={busy}>Close</button>
        </header>
        <ErrorNote error={err} />
        <div className="form-grid">
          <label className="field wide"><span>What is it?</span>
            <input value={form.title} maxLength={160} required autoFocus placeholder="e.g. Website relaunch"
                   onChange={(e) => set("title", e.target.value)} />
          </label>
          <label className="field"><span>Company</span>
            <select value={form.company_id || ""} onChange={(e) => { set("company_id", e.target.value); set("contact_id", ""); }}>
              <option value="">— none —</option>
              {companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          <label className="field"><span>Find a company</span>
            <input value={companyQ} maxLength={80} placeholder="type to search" onChange={(e) => setCompanyQ(e.target.value)} />
          </label>
          <label className="field"><span>Contact person</span>
            <select value={form.contact_id || ""} onChange={(e) => set("contact_id", e.target.value)} disabled={!contacts.length}>
              <option value="">{contacts.length ? "— none —" : form.company_id ? "no people at this company" : "pick a company first"}</option>
              {contacts.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </label>
          <label className="field"><span>Stage</span>
            <select value={form.stage_id || ""} onChange={(e) => set("stage_id", e.target.value)}>
              {stages.map((s) => <option key={s.id} value={s.id}>{s.name}{s.kind === "open" ? ` (${s.probability} %)` : ""}</option>)}
            </select>
          </label>
          <label className="field"><span>Value (CHF)</span>
            <input inputMode="decimal" value={form.value} maxLength={16} placeholder="12000" onChange={(e) => set("value", e.target.value)} />
          </label>
          <label className="field"><span>Probability %</span>
            <input type="number" min={0} max={100} value={form.probability ?? ""} placeholder={stage ? `${stage.probability} (stage)` : ""}
                   onChange={(e) => set("probability", e.target.value)} />
          </label>
          <label className="field"><span>Expected close</span>
            <input type="date" value={form.expected_close || ""} onChange={(e) => set("expected_close", e.target.value)} />
          </label>
          <label className="field"><span>Source</span>
            <input value={form.source} maxLength={200} placeholder="e.g. referral, inquiry" onChange={(e) => set("source", e.target.value)} />
          </label>
          <label className="field"><span>Next step</span>
            <input value={form.next_step} maxLength={200} placeholder="e.g. Send the offer" onChange={(e) => set("next_step", e.target.value)} />
          </label>
          <label className="field"><span>By</span>
            <input type="date" value={form.next_step_at || ""} onChange={(e) => set("next_step_at", e.target.value)} />
          </label>
          <label className="field"><span>Owner</span>
            <select value={form.owner_id || ""} onChange={(e) => set("owner_id", e.target.value)}>
              <option value="">{isNew ? "me" : "— nobody —"}</option>
              {(status?.people || []).map((p) => <option key={p.id} value={p.id}>{p.display_name}</option>)}
            </select>
          </label>
          <label className="field"><span>Group</span>
            <select value={form.group_id || ""} onChange={(e) => set("group_id", e.target.value)}>
              <option value="">— none —</option>
              {(status?.groups || []).map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
            </select>
          </label>
          <label className="field wide"><span>Notes</span>
            <textarea rows={3} value={form.notes} maxLength={5000} onChange={(e) => set("notes", e.target.value)} />
          </label>
        </div>
        <div className="modal-actions">
          <button type="button" className="btn ghost small" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="submit" className="btn primary small" disabled={busy}>{isNew ? "Add deal" : "Save"}</button>
        </div>
      </form>
    </div>
  );
}
