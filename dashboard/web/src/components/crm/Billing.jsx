import { useState } from "react";
import { api } from "../../api.js";
import { fmtCHF, fmtDate } from "../../format.js";
import { usePoll, useStoredState } from "../../hooks.js";
import { Empty, ErrorNote, Tag } from "../common.jsx";
import { Icon } from "../icons.jsx";

/* Quotes & invoices: totals, the list by state, and new documents. A document opens in BillingDoc. */

export const STATE = {
  draft: ["Draft", ""], pending: ["Waiting for approval", "busy"], open: ["Open", ""], overdue: ["Overdue", "bad"],
  partly_paid: ["Partly paid", "busy"], paid: ["Paid", "ok"], sent: ["Sent", ""], accepted: ["Accepted", "ok"],
  declined: ["Declined", "bad"], expired: ["Expired", "bad"], cancelled: ["Cancelled", "bad"],
};
const FILTERS = {
  invoice: [["", "All"], ["draft", "Drafts"], ["unpaid", "Unpaid"], ["overdue", "Overdue"], ["paid", "Paid"]],
  quote: [["", "All"], ["draft", "Drafts"], ["sent", "Sent"], ["accepted", "Accepted"], ["declined", "Declined"], ["expired", "Expired"]],
};

export function StateTag({ d }) {
  const [label, tone] = STATE[d.state] || [d.state, ""];
  return <Tag kind={tone}>{label}</Tag>;
}

export default function Billing({ billing, onOpen, onNew, onSettings }) {
  const [kind, setKind] = useStoredState("alfred-billing-kind", "invoice");
  const [state, setState] = useState("");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const p = new URLSearchParams({ kind });
  if (state) p.set("state", state);
  if (query) p.set("q", query);
  const { data, error } = usePoll(() => api(`/billing/docs?${p}`), 30000, [p.toString()]);
  const s = billing?.summary;
  const items = data?.items || [];
  const year = new Date().getFullYear();
  return (
    <div className="crm-billing">
      {billing?.missing?.length > 0 && (
        <div className="crm-callout warn">
          <span className="crm-callout-label">Before the first invoice</span>
          <b>Billing settings are missing: {billing.missing.join(", ")}.</b>
          {billing.can.settings ? <button className="btn small" onClick={onSettings}>Open settings</button>
            : <span className="muted small">Ask someone who manages billing to fill them in.</span>}
        </div>
      )}
      {s && (
        <div className="crm-kpis">
          <button type="button" className="crm-kpi as-btn" onClick={() => { setKind("invoice"); setState("unpaid"); }}>
            <span>Receivable</span><b>{fmtCHF(s.receivable)}</b><em>{s.unpaid_count} unpaid invoice{s.unpaid_count === 1 ? "" : "s"}</em></button>
          <button type="button" className={`crm-kpi as-btn${s.overdue_count ? " warn" : ""}`} onClick={() => { setKind("invoice"); setState("overdue"); }}>
            <span>Overdue</span><b>{fmtCHF(s.overdue)}</b><em>{s.overdue_count} invoice{s.overdue_count === 1 ? "" : "s"}</em></button>
          <div className="crm-kpi ok"><span>Paid this month</span><b>{fmtCHF(s.paid_month)}</b><em>payments recorded</em></div>
          <button type="button" className="crm-kpi as-btn" onClick={() => { setKind("quote"); setState("sent"); }}>
            <span>Open quotes</span><b>{fmtCHF(s.quotes_open_value)}</b><em>{s.quotes_open} waiting for an answer</em></button>
          {(s.drafts > 0 || s.pending > 0) && (
            <button type="button" className="crm-kpi as-btn" onClick={() => setState("draft")}>
              <span>To finish</span><b>{s.drafts}</b><em>draft{s.drafts === 1 ? "" : "s"}{s.pending ? ` · ${s.pending} waiting for approval` : ""}</em></button>
          )}
        </div>
      )}
      <div className="crm-billing-tools">
        <a className="btn ghost small" href={`/api/billing/export?kind=${kind}&year=${year}`} download>Export {year} (CSV)</a>
        {billing?.can?.settings && <button className="btn ghost small" onClick={onSettings}><Icon name="settings" className="btn-ico" />Settings</button>}
        <button className="btn primary small" onClick={() => onNew(kind)}><Icon name="plus" className="btn-ico" />New {kind}</button>
      </div>
      <div className="mail-toolbar">
        <div className="mail-filters">
          <div className="seg" role="tablist" aria-label="Kind">
            {[["invoice", "Invoices"], ["quote", "Quotes"]].map(([k, l]) => (
              <button key={k} role="tab" aria-selected={kind === k} className={kind === k ? "active" : ""}
                      onClick={() => { setKind(k); setState(""); }}>{l}</button>
            ))}
          </div>
          <form className="mail-search" onSubmit={(e) => { e.preventDefault(); setQuery(q.trim()); }}>
            <input value={q} maxLength={80} onChange={(e) => setQ(e.target.value)} placeholder="Number, title or client" aria-label="Search" />
            {query && <button type="button" className="btn ghost small" onClick={() => { setQ(""); setQuery(""); }}>Clear</button>}
          </form>
        </div>
      </div>
      <div className="crm-filters" role="tablist" aria-label="Filter">
        {FILTERS[kind].map(([k, l]) => (
          <button key={k || "all"} type="button" role="tab" aria-selected={state === k} className={`chip${state === k ? "" : " off"}`}
                  onClick={() => setState(k)}>{l}</button>
        ))}
      </div>
      <ErrorNote error={error} />
      {data && items.length === 0 && (
        state || query ? <Empty>Nothing here.</Empty> : (
          <div className="crm-empty">
            <Icon name="doc" className="crm-empty-ico" />
            <p><b>No {kind === "invoice" ? "invoices" : "quotes"} yet.</b></p>
            <p className="muted small">Write one here, from a company or a deal, or tell Alfred: “invoice Acme 10 hours of web design”.</p>
            <button className="btn primary small" onClick={() => onNew(kind)}><Icon name="plus" className="btn-ico" />New {kind}</button>
          </div>
        )
      )}
      <div className="mail-list">
        {items.map((d) => (
          <button key={d.id} type="button" className="crm-row" onClick={() => onOpen(d.id)}>
            <span className={`crm-avatar doc s-${d.state}`} aria-hidden="true"><Icon name="doc" className="lab-ico" /></span>
            <span className="crm-row-main">
              <span className="crm-row-top"><b className="crm-row-name">{d.number || "Draft"}</b><StateTag d={d} />
                {d.reminders > 0 && <span className="meta">{d.reminders} reminder{d.reminders > 1 ? "s" : ""}</span>}</span>
              <span className="meta crm-row-sub">{[d.company_name || d.recipient?.name, d.title].filter(Boolean).join(" · ") || "No recipient yet"}</span>
            </span>
            <span className="crm-row-money"><b>{fmtCHF(d.total)}</b>
              <span className={`meta${d.state === "overdue" ? " warn-text" : ""}`}>
                {d.state === "partly_paid" || d.state === "overdue" ? `${fmtCHF(d.open_amount)} open` : ""}
                {d.due_date && !["paid", "accepted", "declined", "cancelled", "draft"].includes(d.state)
                  ? ` ${d.kind === "invoice" ? "due" : "valid until"} ${fmtDate(d.due_date)}` : d.issue_date ? fmtDate(d.issue_date) : ""}
              </span></span>
          </button>
        ))}
      </div>
    </div>
  );
}
