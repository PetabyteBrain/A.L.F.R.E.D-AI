import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api.js";
import { fmtAgo, fmtCHF, fmtDate } from "../../format.js";
import { usePoll } from "../../hooks.js";
import { ErrorNote } from "../common.jsx";
import { Icon } from "../icons.jsx";
import { StateTag } from "./Billing.jsx";

/* One quote or invoice. Drafts: an editor (client, recipient, lines with products, live totals). Issued: the
   document with its payments and what can happen next (send, remind, record a payment, accept, cancel). */

const EMPTY_LINE = { product_id: "", description: "", quantity: 1, unit: "", unit_price: "", discount: 0, vat_key: "" };

function totals(lines, b) {
  const rates = Object.fromEntries((b?.vat_rates || []).map((r) => [r.key, r]));
  let net = 0;
  const by = {};
  const out = lines.map((ln) => {
    const n = Math.round((Number(ln.quantity || 0) * Number(ln.unit_price || 0) * (100 - Number(ln.discount || 0)) / 100) * 100) / 100;
    net += n;
    if (b?.vat_enabled) {
      const r = rates[ln.vat_key || b.default_vat] || rates[b.default_vat];
      if (r && r.rate) by[r.rate] = (by[r.rate] || 0) + n;
    }
    return n;
  });
  const vat = Object.entries(by).map(([rate, base]) => ({ rate: Number(rate), base, vat: Math.round(base * rate) / 100 }));
  const vatSum = vat.reduce((a, v) => a + v.vat, 0);
  const gross = net + vatSum;
  return { lineNet: out, net, vat, total: Math.round(gross * 20) / 20, gross };
}

function SendDialog({ doc, onDone, onClose }) {
  const [to, setTo] = useState(doc.recipient?.email || "");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  async function send() {
    setBusy(true); setErr("");
    try { onDone(await api(`/billing/docs/${doc.id}/send`, { method: "POST", body: { to, message: msg } })); }
    catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  return (
    <div className="modal-backdrop" role="presentation" onClick={() => !busy && onClose()}>
      <div className="hud modal" role="dialog" aria-modal="true" aria-label="Send" onClick={(e) => e.stopPropagation()}>
        <header className="hud-head"><h2>Send {doc.kind === "invoice" ? "invoice" : "quote"}</h2></header>
        <p className="muted small">It goes out in the company's email layout with the PDF attached{doc.kind === "invoice" ? " (with the QR-bill)" : ""},
          after someone with approval rights confirms it{doc.status === "draft" ? "; it gets its number then" : ""}. You may approve your own request.</p>
        <label className="field"><span>To</span><input type="email" value={to} maxLength={254} onChange={(e) => setTo(e.target.value)} /></label>
        <label className="field"><span>Message (optional, instead of the standard text)</span>
          <textarea rows={4} value={msg} maxLength={2000} onChange={(e) => setMsg(e.target.value)} /></label>
        <ErrorNote error={err} />
        <div className="modal-actions">
          <button className="btn ghost small" onClick={onClose} disabled={busy}>Cancel</button>
          <button className="btn primary small" onClick={send} disabled={busy || !to.trim()}><Icon name="send" className="btn-ico" />Ask for approval</button>
        </div>
      </div>
    </div>
  );
}

function LinesEditor({ lines, setLines, billing }) {
  const t = totals(lines, billing);
  const products = billing?.products || [];
  const set = (i, patch) => setLines(lines.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  const pick = (i, pid) => {
    const p = products.find((x) => String(x.id) === String(pid));
    set(i, p ? { product_id: p.id, description: p.name + (p.description ? `\n${p.description}` : ""), unit: p.unit,
                 unit_price: p.unit_price, vat_key: p.vat_key } : { product_id: "" });
  };
  const move = (i, d) => { const n = [...lines]; [n[i], n[i + d]] = [n[i + d], n[i]]; setLines(n); };
  return (
    <div className="bill-lines">
      <div className="bill-line head" aria-hidden="true">
        <span>Description</span><span>Qty</span><span>Unit</span><span>Price</span><span>Disc. %</span>
        {billing?.vat_enabled && <span>VAT</span>}<span className="r">Amount</span><span />
      </div>
      {lines.map((ln, i) => (
        <div key={i} className={`bill-line${billing?.vat_enabled ? " vat" : ""}`}>
          <div className="bill-desc">
            {products.length > 0 && (
              <select value={ln.product_id || ""} onChange={(e) => pick(i, e.target.value)} aria-label="Product or service">
                <option value="">— free text —</option>
                {products.map((p) => <option key={p.id} value={p.id}>{p.name} · {fmtCHF(p.unit_price)}{p.unit ? `/${p.unit}` : ""}</option>)}
              </select>
            )}
            <textarea rows={ln.description.includes("\n") ? 2 : 1} value={ln.description} maxLength={1000} placeholder="What was done or delivered"
                      onChange={(e) => set(i, { description: e.target.value })} aria-label="Description" />
          </div>
          <label className="bill-cell"><span>Qty</span>
            <input inputMode="decimal" value={ln.quantity} onChange={(e) => set(i, { quantity: e.target.value })} aria-label="Quantity" /></label>
          <label className="bill-cell"><span>Unit</span>
            <input value={ln.unit} maxLength={16} placeholder="h" onChange={(e) => set(i, { unit: e.target.value })} aria-label="Unit" /></label>
          <label className="bill-cell"><span>Price</span>
            <input inputMode="decimal" value={ln.unit_price} placeholder="0.00" onChange={(e) => set(i, { unit_price: e.target.value })} aria-label="Unit price" /></label>
          <label className="bill-cell"><span>Disc. %</span>
            <input inputMode="decimal" value={ln.discount} onChange={(e) => set(i, { discount: e.target.value })} aria-label="Discount %" /></label>
          {billing?.vat_enabled && (
            <label className="bill-cell"><span>VAT</span>
              <select value={ln.vat_key || billing.default_vat} onChange={(e) => set(i, { vat_key: e.target.value })} aria-label="VAT">
                {billing.vat_rates.map((r) => <option key={r.key} value={r.key}>{r.rate} %</option>)}
              </select></label>
          )}
          <span className="bill-amount">{fmtCHF(t.lineNet[i])}</span>
          <span className="bill-line-tools">
            <button type="button" className="btn ghost small" disabled={i === 0} onClick={() => move(i, -1)} aria-label="Move up">↑</button>
            <button type="button" className="btn ghost small" disabled={i === lines.length - 1} onClick={() => move(i, 1)} aria-label="Move down">↓</button>
            <button type="button" className="btn ghost small" onClick={() => setLines(lines.filter((_, j) => j !== i))} aria-label="Remove line">✕</button>
          </span>
        </div>
      ))}
      <button type="button" className="btn ghost small" disabled={lines.length >= 100}
              onClick={() => setLines([...lines, { ...EMPTY_LINE, vat_key: billing?.default_vat || "" }])}><Icon name="plus" className="btn-ico" />Add a line</button>
      <dl className="bill-totals">
        <dt>Subtotal{billing?.vat_enabled ? " (excl. VAT)" : ""}</dt><dd>{fmtCHF(t.net)}</dd>
        {t.vat.map((v) => <Fragment key={v.rate}><dt>VAT {v.rate} % on {fmtCHF(v.base)}</dt><dd>{fmtCHF(v.vat)}</dd></Fragment>)}
        {Math.abs(t.total - t.gross) > 0.001 && <><dt>Rounding</dt><dd>{fmtCHF(t.total - t.gross)}</dd></>}
        <dt className="total">Total</dt><dd className="total">{fmtCHF(t.total)}</dd>
      </dl>
    </div>
  );
}

function Editor({ doc, billing, onSaved, onToast, setErr, saveRef }) {
  const [f, setF] = useState(() => ({
    company_id: doc.company_id || "", contact_id: doc.contact_id || "", deal_id: doc.deal_id || "", title: doc.title || "",
    intro: doc.intro || "", due_date: doc.due_date || "", recipient: { ...doc.recipient },
    lines: doc.lines.length ? doc.lines.map((l) => ({ ...EMPTY_LINE, ...l, product_id: l.product_id || "" })) : [{ ...EMPTY_LINE, vat_key: billing?.default_vat || "" }],
  }));
  const [companies, setCompanies] = useState(doc.company_id ? [{ id: doc.company_id, name: doc.company_name }] : []);
  const [cq, setCq] = useState("");
  const [contacts, setContacts] = useState([]);
  const [deals, setDeals] = useState([]);
  const [busy, setBusy] = useState(false);
  const set = (k, v) => setF((x) => ({ ...x, [k]: v }));
  const setRec = (k, v) => setF((x) => ({ ...x, recipient: { ...x.recipient, [k]: v } }));

  useEffect(() => {
    if (!cq.trim()) return undefined;
    const t = setTimeout(() => api(`/crm/companies?limit=8&q=${encodeURIComponent(cq.trim())}`)
      .then((d) => setCompanies((o) => [...o.filter((c) => String(c.id) === String(f.company_id)), ...d.items.filter((c) => String(c.id) !== String(f.company_id))]))
      .catch(() => {}), 250);
    return () => clearTimeout(t);
  }, [cq, f.company_id]);
  useEffect(() => {
    if (!f.company_id) { setContacts([]); setDeals([]); return; }
    api(`/crm/contacts?limit=100&company_id=${Number(f.company_id)}`).then((d) => setContacts(d.items)).catch(() => {});
    api(`/crm/deals?status=all&limit=100&company_id=${Number(f.company_id)}`).then((d) => setDeals(d.items)).catch(() => {});
  }, [f.company_id]);

  async function fillRecipient(companyId, contactId) {
    try {
      const p = new URLSearchParams();
      if (companyId) p.set("company_id", companyId);
      if (contactId) p.set("contact_id", contactId);
      const r = await api(`/billing/recipient?${p}`);
      setF((x) => ({ ...x, recipient: r }));
    } catch (e) { setErr(e.message); }
  }
  async function save(quiet = false) {
    setBusy(true); setErr("");
    try {
      const fields = { ...f, company_id: f.company_id ? Number(f.company_id) : null, contact_id: f.contact_id ? Number(f.contact_id) : null,
                       deal_id: f.deal_id ? Number(f.deal_id) : null, due_date: f.due_date || null,
                       lines: f.lines.filter((l) => l.description.trim() || l.product_id).map((l) => ({ ...l, product_id: l.product_id ? Number(l.product_id) : null })) };
      const out = await api(`/billing/docs/${doc.id}`, { method: "PATCH", body: { fields } });
      onSaved(out); if (!quiet) onToast("Saved");
      return true;
    } catch (e) { setErr(e.message); return false; } finally { setBusy(false); }
  }
  saveRef.current = () => save(true);       // Send / Issue / Preview save the latest edits first
  const rec = f.recipient || {};
  return (
    <div className="bill-editor">
      <div className="form-grid">
        <label className="field"><span>Client (company)</span>
          <select value={f.company_id || ""} onChange={(e) => { set("company_id", e.target.value); set("contact_id", ""); set("deal_id", ""); fillRecipient(e.target.value, ""); }}>
            <option value="">— none —</option>
            {companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </label>
        <label className="field"><span>Find a company</span>
          <input value={cq} maxLength={80} placeholder="type to search" onChange={(e) => setCq(e.target.value)} /></label>
        <label className="field"><span>For the attention of</span>
          <select value={f.contact_id || ""} disabled={!contacts.length} onChange={(e) => { set("contact_id", e.target.value); fillRecipient(f.company_id, e.target.value); }}>
            <option value="">{contacts.length ? "— nobody in particular —" : "no people at this company"}</option>
            {contacts.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </label>
        <label className="field"><span>Deal</span>
          <select value={f.deal_id || ""} disabled={!deals.length} onChange={(e) => set("deal_id", e.target.value)}>
            <option value="">{deals.length ? "— none —" : "no deals"}</option>
            {deals.map((d) => <option key={d.id} value={d.id}>{d.title} · {d.stage_name}</option>)}
          </select>
        </label>
      </div>
      <details className="bill-recipient" open={!rec.name || !rec.pcode}>
        <summary>Address on the {doc.kind}: <b>{[rec.name, rec.attention && `attn. ${rec.attention}`, `${rec.street || ""} ${rec.house_num || ""}`.trim(), `${rec.pcode || ""} ${rec.city || ""}`.trim()].filter(Boolean).join(", ") || "not filled in"}</b></summary>
        <div className="form-grid">
          <label className="field wide"><span>Name</span><input value={rec.name || ""} maxLength={70} onChange={(e) => setRec("name", e.target.value)} /></label>
          <label className="field wide"><span>For the attention of</span><input value={rec.attention || ""} maxLength={70} onChange={(e) => setRec("attention", e.target.value)} /></label>
          <label className="field"><span>Street</span><input value={rec.street || ""} maxLength={70} onChange={(e) => setRec("street", e.target.value)} /></label>
          <label className="field"><span>No.</span><input value={rec.house_num || ""} maxLength={16} onChange={(e) => setRec("house_num", e.target.value)} /></label>
          <label className="field"><span>Postcode</span><input value={rec.pcode || ""} maxLength={16} onChange={(e) => setRec("pcode", e.target.value)} /></label>
          <label className="field"><span>Town</span><input value={rec.city || ""} maxLength={35} onChange={(e) => setRec("city", e.target.value)} /></label>
          <label className="field"><span>Country</span><input value={rec.country || "CH"} maxLength={2} onChange={(e) => setRec("country", e.target.value.toUpperCase())} /></label>
          <label className="field"><span>Email (for sending)</span><input type="email" value={rec.email || ""} maxLength={254} onChange={(e) => setRec("email", e.target.value)} /></label>
        </div>
      </details>
      <div className="form-grid">
        <label className="field"><span>Title</span>
          <input value={f.title} maxLength={160} placeholder={doc.kind === "invoice" ? "e.g. Website relaunch, October" : "e.g. Online shop"}
                 onChange={(e) => set("title", e.target.value)} /></label>
        <label className="field"><span>{doc.kind === "invoice" ? "Payable by" : "Valid until"}</span>
          <input type="date" value={f.due_date || ""} onChange={(e) => set("due_date", e.target.value)} />
          <span className="muted tiny">Empty = {doc.kind === "invoice" ? billing?.payment_days : billing?.quote_valid_days} days after it's issued.</span></label>
        <label className="field wide"><span>Introduction (optional)</span>
          <textarea rows={2} value={f.intro} maxLength={2000} placeholder="e.g. As agreed, we invoice the following work." onChange={(e) => set("intro", e.target.value)} /></label>
      </div>
      <h3 className="sub">Lines</h3>
      <LinesEditor lines={f.lines} setLines={(v) => set("lines", v)} billing={billing} />
      <div className="bill-save-row">
        <span className="muted tiny">Totals here are a preview; saving calculates them exactly (VAT per rate{billing?.vat_enabled ? "" : ", no VAT"}, rounded to 5 Rappen).</span>
        <button className="btn primary small" disabled={busy} onClick={() => save()}>Save draft</button>
      </div>
    </div>
  );
}

export default function BillingDoc({ id, billing, onBack, onOpenRecord, onOpenDoc, onToast, onChanged }) {
  const { data: d, error, reload } = usePoll(() => api(`/billing/docs/${id}`), 0, [id]);
  const [err, setErr] = useState("");
  const [dialog, setDialog] = useState(null);
  const [pay, setPay] = useState({ amount: "", paid_on: "" });
  const [rev, setRev] = useState(0);
  const saveRef = useRef(null);
  const saveFirst = async () => !d?.editable || !saveRef.current || saveRef.current();
  const changed = (text) => { reload(); setRev((n) => n + 1); onChanged?.(); if (text) onToast(text); };
  async function act(path, body, text) {
    setErr("");
    try {
      const out = await api(`/billing/docs/${id}/${path}`, { method: "POST", body: body || {} });
      if (out?.status === "pending" || out?.area) onToast(out.already_pending ? "Already waiting for approval" : "Sent for approval");
      else if (text) onToast(text);
      if (out?.id && out?.kind && out.id !== id) { onOpenDoc(out.id); return; }
      changed();
    } catch (e) { setErr(e.message); }
  }
  const lines = useMemo(() => d?.lines || [], [d]);
  if (!d) return <div className="crm-detail"><ErrorNote error={error} />{!error && <p className="muted">Loading…</p>}</div>;
  const isInvoice = d.kind === "invoice";
  const open = ["open", "overdue", "partly_paid"].includes(d.state);
  return (
    <div className="crm-detail bill-doc">
      <div className="mail-view-head">
        <div className="mail-view-nav"><button className="btn ghost small" onClick={onBack}>‹ Back</button></div>
        <div className="mail-view-tools">
          <button className="btn small" onClick={async () => {
            const tab = window.open("about:blank", "_blank");           // opened now, so pop-up blockers allow it
            if (await saveFirst()) { if (tab) tab.location = `/api/billing/docs/${id}/pdf`; } else if (tab) tab.close();
          }}><Icon name="doc" className="btn-ico" />{d.editable ? "Preview PDF" : "PDF"}</button>
          {d.editable && <button className="btn small" onClick={async () => { if (await saveFirst()) setDialog("issue"); }}>Issue without sending</button>}
          {(d.editable || (d.status === "issued" && !d.sent_email_id)) && !d.pending_approval && (
            <button className="btn primary small" onClick={async () => { if (await saveFirst()) setDialog("send"); }}><Icon name="send" className="btn-ico" />Send…</button>)}
          {isInvoice && open && d.sent_email_id && (
            <button className="btn small" onClick={() => act("remind", {}, "")}>Payment reminder…</button>)}
          {!isInvoice && d.status === "issued" && (
            <>
              <button className="btn small" onClick={() => act("outcome", { accepted: true }, "Quote accepted")}>Accepted</button>
              <button className="btn ghost small" onClick={() => act("outcome", { accepted: false }, "Quote declined")}>Declined</button>
            </>
          )}
          {!isInvoice && ["issued", "accepted"].includes(d.status) && (
            <button className="btn small" onClick={() => act("copy", { kind: "invoice" }, "Invoice draft created")}>Create invoice</button>)}
        </div>
      </div>

      <header className="crm-head">
        <span className={`crm-avatar doc s-${d.state}`} aria-hidden="true"><Icon name="doc" className="lab-ico" /></span>
        <div className="crm-head-main">
          <h3 className="crm-title">{isInvoice ? "Invoice" : "Quote"} {d.number || "(draft)"}</h3>
          <div className="crm-head-meta">
            <StateTag d={d} />
            {d.company_name && <button type="button" className="linkish" onClick={() => onOpenRecord("company", d.company_id)}>{d.company_name}</button>}
            {d.deal_title && <button type="button" className="linkish" onClick={() => onOpenRecord("deal", d.deal_id)}>{d.deal_title}</button>}
            {d.quote && <button type="button" className="linkish" onClick={() => onOpenDoc(d.quote.id)}>from quote {d.quote.number}</button>}
          </div>
        </div>
        <div className="crm-head-money"><b>{fmtCHF(d.total)}</b>
          <span className="meta">{isInvoice && d.paid_amount ? `${fmtCHF(d.open_amount)} open` : d.total_vat ? `incl. ${fmtCHF(d.total_vat)} VAT` : ""}</span></div>
      </header>
      {d.pending_approval && (
        <div className="crm-callout warn"><span className="crm-callout-label">Waiting for approval</span><b>{d.pending_approval.summary}</b></div>
      )}
      <ErrorNote error={err} />

      {d.editable ? (
        <Editor key={rev} doc={d} billing={billing} onSaved={() => reload()} onToast={onToast} setErr={setErr} saveRef={saveRef} />
      ) : (
        <div className="crm-detail-grid">
          <div className="crm-facts-col">
            <div className="crm-facts">
              <div className="kv crm-fact"><span>To</span><b className="pre">{[d.recipient.name, d.recipient.attention && `Attn. ${d.recipient.attention}`,
                `${d.recipient.street || ""} ${d.recipient.house_num || ""}`.trim(), `${d.recipient.pcode || ""} ${d.recipient.city || ""}`.trim()].filter(Boolean).join("\n")}</b></div>
              <div className="kv crm-fact"><span>Issued</span><b>{fmtDate(d.issue_date)}</b></div>
              <div className="kv crm-fact"><span>{isInvoice ? "Payable by" : "Valid until"}</span><b className={d.state === "overdue" ? "warn-text" : ""}>{fmtDate(d.due_date)}</b></div>
              {d.reference && <div className="kv crm-fact"><span>Reference</span><b>{d.reference}</b></div>}
              <div className="kv crm-fact"><span>Sent</span><b>{d.sent_email_id ? `by email to ${d.recipient.email}` : "not by email"}</b></div>
              {d.reminders > 0 && <div className="kv crm-fact"><span>Reminders</span><b>{d.reminders} · last {fmtAgo(d.last_reminder_at)}</b></div>}
            </div>
            {isInvoice && ["issued", "paid"].includes(d.status) && (
              <section className="bill-payments">
                <h3 className="sub">Payments</h3>
                {d.payments.length === 0 && <p className="muted small">None recorded yet.</p>}
                {d.payments.map((p) => (
                  <div key={p.id} className="bill-payment">
                    <b>{fmtCHF(p.amount)}</b><span className="meta">{fmtDate(p.paid_on)}{p.user_name ? ` · ${p.user_name}` : ""}</span>
                    <button className="linkish" onClick={async () => { try { await api(`/billing/payments/${p.id}`, { method: "DELETE" }); changed("Payment removed"); } catch (e) { setErr(e.message); } }}>Remove</button>
                  </div>
                ))}
                {d.status === "issued" && (
                  <form className="bill-pay-form" onSubmit={(e) => { e.preventDefault(); act("payments", { amount: pay.amount || null, paid_on: pay.paid_on || null }, "Payment recorded"); setPay({ amount: "", paid_on: "" }); }}>
                    <input inputMode="decimal" value={pay.amount} placeholder={`${d.open_amount.toFixed(2)} (all)`} onChange={(e) => setPay({ ...pay, amount: e.target.value })} aria-label="Amount" />
                    <input type="date" value={pay.paid_on} onChange={(e) => setPay({ ...pay, paid_on: e.target.value })} aria-label="Paid on" />
                    <button className="btn small" type="submit">Record payment</button>
                  </form>
                )}
              </section>
            )}
            {d.invoices?.length > 0 && (
              <section><h3 className="sub">Invoices from this quote</h3>
                {d.invoices.map((i) => <button key={i.id} className="linkish" onClick={() => onOpenDoc(i.id)}>{i.number || `draft #${i.id}`}</button>)}</section>
            )}
            <div className="bill-more">
              <button className="btn ghost small" onClick={() => act("copy", {}, "Copied as a new draft")}>Copy as new draft</button>
              {["issued", "accepted", "declined"].includes(d.status) && !d.paid_amount && (
                <button className="btn ghost small danger" onClick={() => setDialog("cancel")}>Cancel {d.kind}…</button>)}
            </div>
          </div>
          <div className="bill-view">
            {d.title && <h4 className="bill-title">{d.title}</h4>}
            {d.intro && <p className="pre">{d.intro}</p>}
            <table className="bill-table">
              <thead><tr><th>Description</th><th className="r">Qty</th><th className="r">Price</th>{d.vat_breakdown.length > 0 && <th className="r">VAT</th>}<th className="r">Amount</th></tr></thead>
              <tbody>
                {lines.map((ln) => (
                  <tr key={ln.id}>
                    <td><span className="pre">{ln.description}</span>{ln.discount ? <span className="meta"> · less {ln.discount} %</span> : null}</td>
                    <td className="r">{ln.quantity} {ln.unit}</td><td className="r">{fmtCHF(ln.unit_price)}</td>
                    {d.vat_breakdown.length > 0 && <td className="r">{ln.vat_rate} %</td>}<td className="r">{fmtCHF(ln.net)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <dl className="bill-totals">
              <dt>Subtotal</dt><dd>{fmtCHF(d.total_net)}</dd>
              {d.vat_breakdown.map((v) => <Fragment key={v.rate}><dt>VAT {v.rate} %</dt><dd>{fmtCHF(v.vat)}</dd></Fragment>)}
              {d.rounding ? <><dt>Rounding</dt><dd>{fmtCHF(d.rounding)}</dd></> : null}
              <dt className="total">Total</dt><dd className="total">{fmtCHF(d.total)}</dd>
              {d.paid_amount > 0 && <><dt>Paid</dt><dd>{fmtCHF(-d.paid_amount)}</dd><dt>Open</dt><dd>{fmtCHF(d.open_amount)}</dd></>}
            </dl>
          </div>
        </div>
      )}
      {d.editable && (
        <div className="bill-more">
          <button className="btn ghost small danger" onClick={() => setDialog("discard")}>Discard draft</button>
        </div>
      )}

      {dialog === "send" && <SendDialog doc={d} onClose={() => setDialog(null)} onDone={(a) => { setDialog(null); onToast(a.already_pending ? "Already waiting for approval" : "Sent for approval"); changed(); }} />}
      {dialog === "issue" && (
        <Confirm title={`Issue this ${d.kind}?`} body={`It gets the next number, can't be changed afterwards and is filed as a PDF. Nothing is sent; use this to post or hand it over yourself.`}
                 label="Issue" onClose={() => setDialog(null)} onYes={() => { setDialog(null); act("issue", {}, "Issued"); }} />
      )}
      {dialog === "cancel" && (
        <Confirm title={`Cancel ${d.number}?`} body="The cancellation waits for approval. The number stays used; write a new document if needed."
                 label="Ask for approval" danger withReason onClose={() => setDialog(null)} onYes={(reason) => { setDialog(null); act("cancel", { reason }, ""); }} />
      )}
      {dialog === "discard" && (
        <Confirm title="Discard this draft?" body="It has no number yet and is deleted for good." label="Discard" danger onClose={() => setDialog(null)}
                 onYes={async () => { setDialog(null); try { await api(`/billing/docs/${id}`, { method: "DELETE" }); onToast("Draft discarded"); onChanged?.(); onBack(); } catch (e) { setErr(e.message); } }} />
      )}
    </div>
  );
}

function Confirm({ title, body, label, danger, withReason, onYes, onClose }) {
  const [reason, setReason] = useState("");
  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div className={`hud modal${danger ? " alert" : ""}`} role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <header className="hud-head"><h2>{title}</h2></header>
        <p className="modal-body small">{body}</p>
        {withReason && <input value={reason} maxLength={200} placeholder="Reason (optional)" onChange={(e) => setReason(e.target.value)} aria-label="Reason" />}
        <div className="modal-actions">
          <button className="btn ghost small" onClick={onClose}>Back</button>
          <button className={`btn small ${danger ? "danger" : "primary"}`} onClick={() => onYes(reason)}>{label}</button>
        </div>
      </div>
    </div>
  );
}
