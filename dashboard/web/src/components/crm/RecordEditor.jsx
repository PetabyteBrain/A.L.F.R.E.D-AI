import { useEffect, useRef, useState } from "react";
import { api } from "../../api.js";
import { ErrorNote } from "../common.jsx";

/* New or edit a company / contact. Shows likely duplicates while typing; the server checks again and
   answers 409 with the matches, which offers "Open it" or "Create anyway". */

const EMPTY = {
  company: { name: "", status: "lead", industry: "", website: "", email: "", phone: "", address: "", notes: "",
             tags: [], custom: {}, source: "", owner_id: "", group_id: "", timetrack_tag: "" },
  contact: { first_name: "", last_name: "", company_id: "", title: "", email: "", phone: "", notes: "",
             tags: [], custom: {}, source: "", owner_id: "", group_id: "" },
};
const STATUS_LABEL = { lead: "Lead", prospect: "Prospect", client: "Client", partner: "Partner", supplier: "Supplier",
                       former: "Former", other: "Other" };
export { STATUS_LABEL };

function TagInput({ value, onChange, suggestions }) {
  const [text, setText] = useState("");
  const add = (t) => {
    const v = t.trim().toLowerCase().slice(0, 30);
    if (v && !value.includes(v)) onChange([...value, v]);
    setText("");
  };
  const open = suggestions.filter((s) => !value.includes(s) && (!text || s.includes(text.toLowerCase()))).slice(0, 8);
  return (
    <div className="crm-tags-input">
      <div className="pick-chips">
        {value.map((t) => (
          <button key={t} type="button" className="chip" onClick={() => onChange(value.filter((x) => x !== t))}
                  aria-label={`Remove tag ${t}`}>{t} ✕</button>
        ))}
        <input value={text} maxLength={30} placeholder={value.length ? "add…" : "e.g. vip, web"}
               onChange={(e) => setText(e.target.value.replace(",", ""))}
               onKeyDown={(e) => {
                 if (e.key === "Enter" || e.key === ",") { e.preventDefault(); add(text); }
                 else if (e.key === "Backspace" && !text && value.length) onChange(value.slice(0, -1));
               }} onBlur={() => text && add(text)} aria-label="Add a tag" />
      </div>
      {open.length > 0 && text && (
        <div className="pick-chips">
          {open.map((s) => <button key={s} type="button" className="chip off" onMouseDown={(e) => { e.preventDefault(); add(s); }}>+ {s}</button>)}
        </div>
      )}
    </div>
  );
}

// Extra fields as rows of [name, value] while editing (an object can't hold two half-typed names).
function CustomFields({ rows, onChange }) {
  const set = (i, k, v) => onChange(rows.map((r, j) => (j === i ? [k, v] : r)));
  return (
    <div className="crm-custom">
      {rows.map(([k, v], i) => (
        <div key={i} className="crm-custom-row">
          <input value={k} maxLength={40} placeholder="Field, e.g. VAT number" aria-label="Field name" onChange={(e) => set(i, e.target.value, v)} />
          <input value={v} maxLength={300} placeholder="Value" aria-label="Value" onChange={(e) => set(i, k, e.target.value)} />
          <button type="button" className="btn ghost small" aria-label="Remove field" onClick={() => onChange(rows.filter((_, j) => j !== i))}>✕</button>
        </div>
      ))}
      <button type="button" className="btn ghost small" disabled={rows.length >= 30} onClick={() => onChange([...rows, ["", ""]])}>+ Field</button>
    </div>
  );
}

export default function RecordEditor({ entity, record, status, presetCompany, onSaved, onOpen, onClose }) {
  const isNew = !record;
  const [form, setForm] = useState(() => {
    const base = { ...EMPTY[entity] };
    if (record) for (const k of Object.keys(base)) base[k] = record[k] ?? base[k];
    if (isNew && presetCompany) base.company_id = presetCompany.id;
    base.custom = Object.entries(base.custom || {});
    return base;
  });
  const [companies, setCompanies] = useState(presetCompany ? [presetCompany] : record?.company ? [record.company] : []);
  const [companyQ, setCompanyQ] = useState("");
  const [dups, setDups] = useState([]);
  const [conflict, setConflict] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const first = useRef(null);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));

  useEffect(() => { first.current?.focus(); }, []);
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && !busy) onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  // Live duplicate hint (debounced).
  const key = entity === "company" ? `${form.name}|${form.email}|${form.phone}|${form.website}`
    : `${form.first_name}|${form.last_name}|${form.email}|${form.phone}`;
  useEffect(() => {
    const params = new URLSearchParams({ entity });
    const fields = entity === "company" ? ["name", "email", "phone", "website"] : ["first_name", "last_name", "email", "phone"];
    fields.forEach((f) => { if (String(form[f] || "").trim().length > 1) params.set(f, form[f]); });
    if (record) params.set("exclude", String(record.id));
    if ([...params.keys()].length < 2 + (record ? 1 : 0)) { setDups([]); return undefined; }
    const t = setTimeout(() => api(`/crm/duplicates?${params}`).then(setDups).catch(() => {}), 400);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  // Company picker for contacts.
  useEffect(() => {
    if (entity !== "contact" || companyQ.trim().length < 1) return undefined;
    const t = setTimeout(() => api(`/crm/companies?limit=8&q=${encodeURIComponent(companyQ.trim())}`)
      .then((d) => setCompanies((old) => [...old.filter((c) => c.id === form.company_id), ...d.items.filter((c) => c.id !== form.company_id)]))
      .catch(() => {}), 250);
    return () => clearTimeout(t);
  }, [companyQ, entity, form.company_id]);

  async function save(force = false) {
    setBusy(true); setErr(""); setConflict(null);
    const fields = { ...form };
    for (const k of ["owner_id", "group_id", "company_id"]) {
      if (k in EMPTY[entity]) fields[k] = fields[k] ? Number(fields[k]) : null;
      else delete fields[k];
    }
    fields.custom = Object.fromEntries(fields.custom.filter(([k]) => k.trim()).map(([k, v]) => [k.trim(), v]));
    if (!isNew) {
      for (const k of Object.keys(fields)) {
        const before = record[k] ?? EMPTY[entity][k];
        if (JSON.stringify(before ?? null) === JSON.stringify(fields[k] ?? null)
            || ((before === null || before === "") && (fields[k] === null || fields[k] === ""))) delete fields[k];
      }
    }
    try {
      const out = await api(isNew ? `/crm/${entity === "company" ? "companies" : "contacts"}`
        : `/crm/${entity === "company" ? "companies" : "contacts"}/${record.id}`,
      { method: isNew ? "POST" : "PATCH", body: { fields, ...(isNew ? { force } : {}) } });
      onSaved(out);
    } catch (e) {
      if (e.status === 409 && e.data?.duplicates) setConflict(e.data.duplicates);
      else setErr(e.message);
    } finally { setBusy(false); }
  }

  const title = `${isNew ? "New" : "Edit"} ${entity}`;
  const people = status?.people || [];
  const groups = status?.groups || [];
  return (
    <div className="modal-backdrop" role="presentation" onClick={() => !busy && onClose()}>
      <form className="hud modal editor crm-editor" role="dialog" aria-modal="true" aria-label={title}
            onClick={(e) => e.stopPropagation()} onSubmit={(e) => { e.preventDefault(); save(false); }}>
        <header className="hud-head">
          <h2><span className="hud-code">{entity === "company" ? "CO" : "CT"}</span>{title}</h2>
          <button type="button" className="btn ghost small" onClick={onClose} disabled={busy}>Close</button>
        </header>
        <ErrorNote error={err} />
        {conflict && (
          <div className="crm-dups strong" role="alert">
            <b>This may already exist:</b>
            {conflict.map((d) => (
              <span key={d.id} className="crm-dup">
                {d.name} <span className="muted tiny">({d.reason})</span>
                <button type="button" className="btn ghost small" onClick={() => onOpen(entity, d.id)}>Open it</button>
              </span>
            ))}
            <button type="button" className="btn small" disabled={busy} onClick={() => save(true)}>It's a different one: create anyway</button>
          </div>
        )}

        <div className="form-grid">
          {entity === "company" ? (
            <>
              <label className="field wide"><span>Name</span>
                <input ref={first} value={form.name} maxLength={120} required onChange={(e) => set("name", e.target.value)} />
              </label>
              <label className="field"><span>Status</span>
                <select value={form.status} onChange={(e) => set("status", e.target.value)}>
                  {(status?.statuses || Object.keys(STATUS_LABEL)).map((s) => <option key={s} value={s}>{STATUS_LABEL[s] || s}</option>)}
                </select>
              </label>
              <label className="field"><span>Industry</span>
                <input value={form.industry} maxLength={120} placeholder="e.g. Restaurant" onChange={(e) => set("industry", e.target.value)} />
              </label>
              <label className="field"><span>Website</span>
                <input value={form.website} maxLength={200} placeholder="example.com" onChange={(e) => set("website", e.target.value)} />
              </label>
            </>
          ) : (
            <>
              <label className="field"><span>First name</span>
                <input ref={first} value={form.first_name} maxLength={120} onChange={(e) => set("first_name", e.target.value)} />
              </label>
              <label className="field"><span>Last name</span>
                <input value={form.last_name} maxLength={120} onChange={(e) => set("last_name", e.target.value)} />
              </label>
              <label className="field"><span>Company</span>
                <select value={form.company_id || ""} onChange={(e) => set("company_id", e.target.value)}>
                  <option value="">— none —</option>
                  {companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
              </label>
              <label className="field"><span>Find a company</span>
                <input value={companyQ} maxLength={80} placeholder="type to search" onChange={(e) => setCompanyQ(e.target.value)} />
              </label>
              <label className="field wide"><span>Job title</span>
                <input value={form.title} maxLength={120} placeholder="e.g. Head of marketing" onChange={(e) => set("title", e.target.value)} />
              </label>
            </>
          )}
          <label className="field"><span>Email</span>
            <input type="email" value={form.email} maxLength={254} onChange={(e) => set("email", e.target.value)} />
          </label>
          <label className="field"><span>Phone</span>
            <input value={form.phone} maxLength={40} placeholder="079 000 00 00" onChange={(e) => set("phone", e.target.value)} />
          </label>
          {entity === "company" && (
            <label className="field wide"><span>Address</span>
              <textarea rows={2} value={form.address} maxLength={300} onChange={(e) => set("address", e.target.value)} />
            </label>
          )}
        </div>

        {dups.length > 0 && !conflict && (
          <div className="crm-dups" role="status">
            <span className="muted small">Possibly the same as:</span>
            {dups.map((d) => (
              <span key={d.id} className="crm-dup">{d.name} <span className="muted tiny">({d.reason})</span>
                <button type="button" className="btn ghost small" onClick={() => onOpen(entity, d.id)}>Open</button></span>
            ))}
          </div>
        )}

        <div className="form-grid">
          <label className="field"><span>Owner</span>
            <select value={form.owner_id || ""} onChange={(e) => set("owner_id", e.target.value)}>
              <option value="">{isNew ? "me" : "— nobody —"}</option>
              {people.map((p) => <option key={p.id} value={p.id}>{p.display_name}</option>)}
            </select>
          </label>
          <label className="field"><span>Group</span>
            <select value={form.group_id || ""} onChange={(e) => set("group_id", e.target.value)}>
              <option value="">— none —</option>
              {groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
            </select>
          </label>
          <div className="field wide"><span>Tags</span>
            <TagInput value={form.tags} onChange={(v) => set("tags", v)} suggestions={status?.tags || []} />
          </div>
          <div className="field wide"><span>More fields</span>
            <CustomFields rows={form.custom} onChange={(v) => set("custom", v)} />
          </div>
          <label className="field wide"><span>Notes</span>
            <textarea rows={3} value={form.notes} maxLength={5000} onChange={(e) => set("notes", e.target.value)} />
          </label>
          <label className="field wide"><span>Source</span>
            <input value={form.source} maxLength={120} placeholder="e.g. referral, website, market analysis" onChange={(e) => set("source", e.target.value)} />
          </label>
          {entity === "company" && status?.timetrack && (
            <label className="field wide"><span>Time-tracking tag</span>
              <input value={form.timetrack_tag} maxLength={120} onChange={(e) => set("timetrack_tag", e.target.value)}
                     placeholder={`Empty = sessions tagged “${form.name || "with its name"}”; several: comma-separated`} />
            </label>
          )}
        </div>
        <p className="muted tiny">Who sees it: the owner, whoever added it{form.group_id ? ", everyone in the group" : ""} and people
          allowed to see every record.</p>
        <div className="modal-actions">
          <button type="button" className="btn ghost small" onClick={onClose} disabled={busy}>Cancel</button>
          <button type="submit" className="btn primary small" disabled={busy}>{isNew ? `Add ${entity}` : "Save"}</button>
        </div>
      </form>
    </div>
  );
}
