import { api } from "../../api.js";
import { fmtCHF, fmtDate } from "../../format.js";
import { usePoll, useStoredState } from "../../hooks.js";
import { ErrorNote } from "../common.jsx";
import { Icon } from "../icons.jsx";

/* CRM reports (stage 5): the same numbers as the weekly report Alfred sends on Monday mornings, for any period:
   pipeline, won and lost with their reasons, new business, invoices, hours per client and who's away. */

const PERIODS = [["this_week", "This week"], ["last_week", "Last week"], ["this_month", "This month"],
                 ["last_month", "Last month"], ["this_quarter", "This quarter"], ["last_quarter", "Last quarter"]];
const ACT = { note: "notes", call: "calls", meeting: "meetings", task: "tasks", document: "documents" };

function DealList({ title, tone, block, onOpen }) {
  return (
    <section className={`crm-report-card ${tone}`}>
      <h3 className="sub">{title} <span className="mail-count plain">{block.count}</span><span className="crm-report-sum">{fmtCHF(block.value)}</span></h3>
      {block.deals.length === 0 ? <p className="muted small">None in this period.</p> : (
        <ul className="crm-report-list">
          {block.deals.map((d) => (
            <li key={d.id}>
              <button type="button" className="linkish" onClick={() => onOpen("deal", d.id)}>{d.title}</button>
              <span className="meta">{[d.company, d.owner].filter(Boolean).join(" · ")}</span>
              <b>{fmtCHF(d.value)}</b>
              {d.note && <em className="crm-report-note">“{d.note}”</em>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default function Reports({ onOpen, onSettings, canSettings }) {
  const [period, setPeriod] = useStoredState("alfred-crm-report", "last_week");
  const { data: r, error } = usePoll(() => api(`/crm-actions/report?period=${period}`), 0, [period]);
  const h = r?.hours;
  const maxH = Math.max(1, ...(h?.clients || []).map((c) => c.hours));
  return (
    <div className="crm-reports">
      <div className="mail-toolbar">
        <div className="mail-filters">
          <b className="mail-title">Reports</b>
          <select className="crm-sort" value={period} onChange={(e) => setPeriod(e.target.value)} aria-label="Period">
            {PERIODS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          {r && <span className="meta">{fmtDate(r.period.start)} – {fmtDate(r.period.end)}</span>}
        </div>
        <div className="crm-billing-tools">
          <button className="btn ghost small" onClick={() => window.print()}>Print</button>
          {canSettings && <button className="btn ghost small" onClick={onSettings}><Icon name="settings" className="btn-ico" />Weekly report</button>}
        </div>
      </div>
      <ErrorNote error={error} />
      {!r && !error && <p className="muted">Loading…</p>}
      {r && (
        <>
          <div className="crm-kpis">
            <div className="crm-kpi"><span>Open pipeline</span><b>{fmtCHF(r.pipeline.open_value)}</b>
              <em>{r.pipeline.open_count} deals · {fmtCHF(r.pipeline.weighted, { compact: true })} weighted</em></div>
            <div className="crm-kpi ok"><span>Won</span><b>{fmtCHF(r.won.value)}</b><em>{r.won.count} deal{r.won.count === 1 ? "" : "s"}</em></div>
            <div className={`crm-kpi${r.lost.count ? " warn" : ""}`}><span>Lost</span><b>{fmtCHF(r.lost.value)}</b><em>{r.lost.count} deal{r.lost.count === 1 ? "" : "s"}</em></div>
            <div className="crm-kpi"><span>New</span><b>{r.new_deals.count} deals</b>
              <em>{fmtCHF(r.new_deals.value, { compact: true })} · {r.new_companies.count} companies ({r.new_companies.leads} leads)</em></div>
            <div className="crm-kpi"><span>Win rate</span><b>{r.pipeline.win_rate_90d == null ? "—" : `${r.pipeline.win_rate_90d} %`}</b><em>last 90 days</em></div>
            {r.billing && (
              <div className={`crm-kpi${r.billing.overdue.count ? " warn" : ""}`}><span>Invoiced</span><b>{fmtCHF(r.billing.invoiced.total)}</b>
                <em>{fmtCHF(r.billing.paid, { compact: true })} paid{r.billing.overdue.count ? ` · ${fmtCHF(r.billing.overdue.amount, { compact: true })} overdue` : ""}</em></div>
            )}
          </div>

          <div className="crm-report-grid">
            <DealList title="Won" tone="ok" block={r.won} onOpen={onOpen} />
            <DealList title="Lost" tone="bad" block={r.lost} onOpen={onOpen} />

            <section className="crm-report-card">
              <h3 className="sub">Activity</h3>
              <div className="crm-report-chips">
                {Object.keys(ACT).map((k) => (
                  <span key={k} className="crm-report-chip"><b>{r.activities[k] || 0}</b>{ACT[k]}</span>
                ))}
              </div>
              <p className="muted small">
                {r.lead_suggestions ? `${r.lead_suggestions} suggested leads waiting for review. ` : ""}
                {r.follow_ups ? `${r.follow_ups} follow-ups open across the team.` : "No follow-ups open."}
                {r.pipeline.no_close_date ? ` ${r.pipeline.no_close_date} open deals have no expected close date.` : ""}
              </p>
              {r.new_companies.names.length > 0 && <p className="small"><span className="muted">New companies:</span> {r.new_companies.names.join(", ")}</p>}
            </section>

            {r.billing && (
              <section className="crm-report-card">
                <h3 className="sub">Quotes &amp; invoices</h3>
                <div className="crm-facts">
                  <div className="kv crm-fact"><span>Invoices issued</span><b>{r.billing.invoiced.count} · {fmtCHF(r.billing.invoiced.total)}</b></div>
                  <div className="kv crm-fact"><span>Payments received</span><b>{fmtCHF(r.billing.paid)}</b></div>
                  <div className="kv crm-fact"><span>Overdue now</span><b className={r.billing.overdue.count ? "warn-text" : ""}>{r.billing.overdue.count} · {fmtCHF(r.billing.overdue.amount)}</b></div>
                  <div className="kv crm-fact"><span>Quotes sent</span><b>{r.billing.quotes_sent.count} · {fmtCHF(r.billing.quotes_sent.total)}</b></div>
                  <div className="kv crm-fact"><span>Quotes accepted</span><b>{r.billing.quotes_accepted}</b></div>
                </div>
              </section>
            )}

            {h && (
              <section className="crm-report-card wide">
                <h3 className="sub"><Icon name="calendar" className="lab-ico" />Hours per client
                  {h.total_hours != null && <span className="crm-report-sum">{h.total_hours} h logged</span>}</h3>
                {h.error ? <p className="muted small">{h.error}</p> : h.clients.length === 0 ? (
                  <p className="muted small">No hours on clients in this period. Sessions count for a client when their tag has the
                    client's name or the time-tracking tag set on the company.</p>
                ) : (
                  <>
                    <div className="crm-hbars">
                      {h.clients.slice(0, 12).map((c) => (
                        <button key={c.id} type="button" className="crm-hbar" onClick={() => onOpen("company", c.id)} title={`${c.name}: ${c.hours} h`}>
                          <span className="crm-hbar-name">{c.name}</span>
                          <span className="crm-hbar-track"><span className="crm-hbar-fill" style={{ width: `${(c.hours / maxH) * 100}%` }} /></span>
                          <b>{c.hours} h</b>
                        </button>
                      ))}
                    </div>
                    <p className="muted tiny">{h.client_hours} h on clients · {h.other_hours} h other work</p>
                  </>
                )}
              </section>
            )}

            {r.away_next_7_days && (
              <section className="crm-report-card">
                <h3 className="sub">Away in the next 7 days</h3>
                {r.away_next_7_days.length === 0 ? <p className="muted small">Everyone's in.</p> : (
                  <ul className="crm-report-list">
                    {r.away_next_7_days.map((a, i) => (
                      <li key={i}><b>{a.name}</b><span className="meta">{a.kind}{a.status === "pending" ? " (not approved yet)" : ""}</span>
                        <span>{fmtDate(a.start)}{a.end !== a.start ? ` – ${fmtDate(a.end)}` : ""}</span></li>
                    ))}
                  </ul>
                )}
              </section>
            )}
          </div>
        </>
      )}
    </div>
  );
}
