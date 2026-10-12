import { api } from "../../api.js";
import { fmtCHF } from "../../format.js";
import { usePoll } from "../../hooks.js";
import { Icon } from "../icons.jsx";
import { StateTag } from "./Billing.jsx";

/* Quotes and invoices of a company or a deal, with buttons to write a new one for it. */
export default function DocList({ filter, preset, onOpenDoc, onNewDoc }) {
  const { data } = usePoll(() => api(`/billing/docs?kind=&${filter}`), 0, [filter]);
  const items = data?.items || [];
  return (
    <div className="crm-people">
      <h3 className="sub">Quotes &amp; invoices {items.length > 0 && <span className="mail-count plain">{items.length}</span>}</h3>
      {data && items.length === 0 && <p className="muted small">None yet.</p>}
      {items.map((d) => (
        <button key={d.id} type="button" className="crm-person crm-deal-mini" onClick={() => onOpenDoc(d.id)}>
          <span className="crm-person-main"><b>{d.kind === "invoice" ? "Invoice" : "Quote"} {d.number || "(draft)"} <StateTag d={d} /></b>
            <span className="meta">{fmtCHF(d.total)}{d.title ? ` · ${d.title}` : ""}</span></span>
        </button>
      ))}
      <div className="bill-new-row">
        <button type="button" className="btn ghost small" onClick={() => onNewDoc("quote", preset)}><Icon name="plus" className="btn-ico" />Quote</button>
        <button type="button" className="btn ghost small" onClick={() => onNewDoc("invoice", preset)}><Icon name="plus" className="btn-ico" />Invoice</button>
      </div>
    </div>
  );
}
