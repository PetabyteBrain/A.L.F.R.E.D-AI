import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { fmtCHF } from "../../format.js";
import { ErrorNote, Tag } from "../common.jsx";

/* billing.settings: who bills (address, IBAN), VAT, terms and numbering, the texts, and products & services. */

const TABS = [["company", "Company & bank"], ["vat", "VAT"], ["terms", "Terms & numbers"], ["texts", "Texts"], ["products", "Products"]];

function Products({ rates, defaultVat, vatEnabled }) {
  const [list, setList] = useState(null);
  const [form, setForm] = useState({ name: "", unit: "h", unit_price: "", vat_key: defaultVat, description: "" });
  const [edit, setEdit] = useState(null);
  const [err, setErr] = useState("");
  const load = () => api("/billing/products?all=true").then(setList).catch((e) => setErr(e.message));
  useEffect(() => { load(); }, []);
  async function save(body, id) {
    setErr("");
    try {
      await api(id ? `/billing/products/${id}` : "/billing/products", { method: id ? "PATCH" : "POST", body: { fields: body } });
      load(); return true;
    } catch (e) { setErr(e.message); return false; }
  }
  return (
    <div>
      <p className="muted small">Saved products and services fill invoice lines in one click (and Alfred can use them by name).</p>
      <ErrorNote error={err} />
      <div className="bill-products">
        {(list || []).map((p) => (edit?.id === p.id ? (
          <form key={p.id} className="bill-product-form" onSubmit={async (e) => { e.preventDefault(); if (await save({ name: edit.name, unit: edit.unit, unit_price: edit.unit_price, vat_key: edit.vat_key, description: edit.description }, p.id)) setEdit(null); }}>
            <input value={edit.name} maxLength={120} required onChange={(e) => setEdit({ ...edit, name: e.target.value })} aria-label="Name" />
            <input value={edit.unit} maxLength={16} onChange={(e) => setEdit({ ...edit, unit: e.target.value })} aria-label="Unit" />
            <input inputMode="decimal" value={edit.unit_price} onChange={(e) => setEdit({ ...edit, unit_price: e.target.value })} aria-label="Price" />
            {vatEnabled && <select value={edit.vat_key} onChange={(e) => setEdit({ ...edit, vat_key: e.target.value })} aria-label="VAT">
              {rates.map((r) => <option key={r.key} value={r.key}>{r.rate} %</option>)}</select>}
            <input className="wide" value={edit.description || ""} maxLength={500} placeholder="Description (optional)" onChange={(e) => setEdit({ ...edit, description: e.target.value })} />
            <button className="btn primary small" type="submit">Save</button>
            <button className="btn ghost small" type="button" onClick={() => setEdit(null)}>Cancel</button>
          </form>
        ) : (
          <div key={p.id} className={`bill-product${p.active ? "" : " off"}`}>
            <b>{p.name}</b><span className="meta">{p.description}</span>
            <span>{fmtCHF(p.unit_price)}{p.unit ? ` / ${p.unit}` : ""}</span>
            {vatEnabled && <Tag>{(rates.find((r) => r.key === p.vat_key) || {}).rate ?? "?"} %</Tag>}
            <button className="btn ghost small" onClick={() => setEdit({ ...p })}>Edit</button>
            <button className="btn ghost small" onClick={() => save({ active: !p.active }, p.id)}>{p.active ? "Hide" : "Show"}</button>
          </div>
        )))}
      </div>
      <form className="bill-product-form" onSubmit={async (e) => { e.preventDefault(); if (await save(form)) setForm({ ...form, name: "", unit_price: "", description: "" }); }}>
        <input value={form.name} maxLength={120} required placeholder="New: e.g. Web design" onChange={(e) => setForm({ ...form, name: e.target.value })} aria-label="Name" />
        <input value={form.unit} maxLength={16} placeholder="unit" onChange={(e) => setForm({ ...form, unit: e.target.value })} aria-label="Unit" />
        <input inputMode="decimal" value={form.unit_price} placeholder="price CHF" onChange={(e) => setForm({ ...form, unit_price: e.target.value })} aria-label="Price" />
        {vatEnabled && <select value={form.vat_key} onChange={(e) => setForm({ ...form, vat_key: e.target.value })} aria-label="VAT">
          {rates.map((r) => <option key={r.key} value={r.key}>{r.rate} %</option>)}</select>}
        <button className="btn primary small" type="submit">Add</button>
      </form>
    </div>
  );
}

export default function BillingSettings({ onClose, onChanged }) {
  const [s, setS] = useState(null);
  const [tab, setTab] = useState("company");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [ok, setOk] = useState("");
  useEffect(() => { api("/billing/settings").then(setS).catch((e) => setErr(e.message)); }, []);
  const set = (k, v) => setS((x) => ({ ...x, [k]: v }));
  async function save() {
    setBusy(true); setErr(""); setOk("");
    try { setS(await api("/billing/settings", { method: "PUT", body: { settings: s } })); setOk("Saved."); onChanged?.(); }
    catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  const field = (k, label, props = {}) => (
    <label className={`field${props.wide ? " wide" : ""}`}><span>{label}</span>
      <input value={s[k] ?? ""} maxLength={props.max || 70} placeholder={props.ph || ""} onChange={(e) => set(k, e.target.value)} /></label>
  );
  return (
    <div className="modal-backdrop" role="presentation" onClick={() => !busy && onClose()}>
      <div className="hud modal editor crm-editor tabbed" role="dialog" aria-modal="true" aria-label="Billing settings" onClick={(e) => e.stopPropagation()}>
        <header className="hud-head"><h2>Billing settings</h2><button className="btn ghost small" onClick={onClose} disabled={busy}>Close</button></header>
        <div className="seg roles-tabs" role="tablist">
          {TABS.map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>{l}</button>)}
        </div>
        <ErrorNote error={err} />
        {ok && <div className="notice" role="status" onClick={() => setOk("")}>{ok}</div>}
        {!s ? <p className="muted">Loading…</p> : tab === "products" ? (
          <Products rates={s.vat_rates} defaultVat={s.default_vat} vatEnabled={s.vat_enabled} />
        ) : (
          <>
            {tab === "company" && (
              <div className="form-grid">
                <p className="muted small wide">Printed on every quote and invoice and used for the QR-bill (the payment slip needs the
                  full address and a Swiss IBAN or QR-IBAN).</p>
                {field("name", "Company name", { wide: true })}
                {field("street", "Street")}{field("house_num", "No.", { max: 16 })}
                {field("pcode", "Postcode", { max: 16 })}{field("city", "Town")}
                {field("country", "Country", { max: 2, ph: "CH" })}
                {field("iban", "IBAN or QR-IBAN", { max: 40, ph: "CH.. .... .... .... .... ." })}
                {field("email", "Email on documents")}{field("phone", "Phone")}
                {field("website", "Website", { wide: true })}
              </div>
            )}
            {tab === "vat" && (
              <div className="form-grid">
                <label className="toggle wide"><input type="checkbox" checked={!!s.vat_enabled} onChange={(e) => set("vat_enabled", e.target.checked)} />
                  <span>{s.vat_enabled ? "Registered for VAT: prices are net, VAT is added per rate" : "Not registered for VAT (documents say so)"}</span></label>
                {field("vat_number", "VAT number", { wide: true, ph: "CHE-123.456.789 MWST" })}
                <div className="field wide"><span>Rates</span>
                  {s.vat_rates.map((r, i) => (
                    <div key={i} className="bill-rate">
                      <input value={r.label} maxLength={30} aria-label="Label" onChange={(e) => set("vat_rates", s.vat_rates.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))} />
                      <input type="number" step="0.1" min={0} max={30} value={r.rate} aria-label="Rate %" onChange={(e) => set("vat_rates", s.vat_rates.map((x, j) => (j === i ? { ...x, rate: e.target.value } : x)))} />
                      <span className="meta">%</span>
                      <label className="mail-unread-toggle"><input type="radio" name="defvat" checked={s.default_vat === r.key} onChange={() => set("default_vat", r.key)} /> default</label>
                    </div>
                  ))}
                  <span className="muted tiny">Swiss rates since 2024: 8.1 % standard, 2.6 % reduced, 3.8 % accommodation.</span>
                </div>
              </div>
            )}
            {tab === "terms" && (
              <div className="form-grid">
                <label className="field"><span>Payment terms (days)</span><input type="number" min={0} max={365} value={s.payment_days} onChange={(e) => set("payment_days", e.target.value)} /></label>
                <label className="field"><span>Quotes valid for (days)</span><input type="number" min={0} max={365} value={s.quote_valid_days} onChange={(e) => set("quote_valid_days", e.target.value)} /></label>
                {field("invoice_format", "Invoice numbers", { max: 40 })}{field("quote_format", "Quote numbers", { max: 40 })}
                <p className="muted tiny wide">{"{year}"} = the year, {"{seq:04d}"} = the running number with 4 digits (starts again each year).
                  Example: R-{new Date().getFullYear()}-0001.</p>
                <label className="toggle wide"><input type="checkbox" checked={!!s.round_05} onChange={(e) => set("round_05", e.target.checked)} />
                  <span>Round totals to 5 Rappen</span></label>
              </div>
            )}
            {tab === "texts" && (
              <div className="form-grid">
                <p className="muted small wide">Placeholders: {"{number} {total} {due_date} {valid_until} {days} {name} {company} {title}"}.</p>
                {[["invoice_footer", "Invoice footer"], ["quote_footer", "Quote footer"], ["invoice_email", "Email with an invoice"],
                  ["quote_email", "Email with a quote"], ["reminder_email", "Payment reminder email"]].map(([k, l]) => (
                  <label key={k} className="field wide"><span>{l}</span>
                    <textarea rows={k.endsWith("email") ? 4 : 2} value={s[k]} maxLength={2000} onChange={(e) => set(k, e.target.value)} /></label>
                ))}
              </div>
            )}
            <div className="modal-actions">
              <button className="btn ghost small" onClick={onClose} disabled={busy}>Close</button>
              <button className="btn primary small" onClick={save} disabled={busy}>Save settings</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
