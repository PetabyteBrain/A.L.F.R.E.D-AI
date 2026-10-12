import { useState } from "react";
import { api } from "../../api.js";
import { fmtCHF, fmtDate } from "../../format.js";
import { usePoll, useStoredState } from "../../hooks.js";
import { Empty, ErrorNote, Tag } from "../common.jsx";
import { Icon } from "../icons.jsx";

/* The sales pipeline: a forecast strip, then the deals as a board (drag a card to another stage, or use its
   "Move" menu) or as a list. Moving to Won or Lost asks why (optional). */

export const LOST_REASONS = ["Price", "Timing", "Chose someone else", "No budget", "No response", "Not a fit"];

function Forecast({ f }) {
  if (!f) return null;
  const max = Math.max(1, ...f.months.map((m) => Math.max(m.value, m.won)));
  const label = (m) => new Date(`${m.month}-15T12:00:00`).toLocaleDateString([], { month: "short" });
  return (
    <section className="crm-forecast" aria-label="Forecast">
      <div className="crm-kpis">
        <div className="crm-kpi"><span>Open pipeline</span><b>{fmtCHF(f.open_value)}</b><em>{f.open_count} deals</em></div>
        <div className="crm-kpi"><span>Weighted</span><b>{fmtCHF(f.weighted)}</b><em>by stage probability</em></div>
        <div className="crm-kpi ok"><span>Won this month</span><b>{fmtCHF(f.won_month)}</b><em>quarter {fmtCHF(f.won_quarter, { compact: true })}</em></div>
        <div className="crm-kpi"><span>Win rate</span><b>{f.win_rate_90d == null ? "—" : `${f.win_rate_90d} %`}</b><em>last 90 days</em></div>
        {(f.overdue.count > 0 || f.next_steps_due > 0) && (
          <div className="crm-kpi warn"><span>Needs attention</span>
            <b>{f.overdue.count + f.next_steps_due}</b>
            <em>{[f.overdue.count && `${f.overdue.count} past close date`, f.next_steps_due && `${f.next_steps_due} next steps due`].filter(Boolean).join(" · ")}</em>
          </div>
        )}
      </div>
      <div className="crm-bars" role="img" aria-label="Expected closes by month: value, weighted and won">
        {f.months.map((m) => (
          <div key={m.month} className="crm-bar-col" title={`${label(m)}: ${m.count} deals, ${fmtCHF(m.value)} (weighted ${fmtCHF(m.weighted)}); won ${fmtCHF(m.won)}`}>
            <div className="crm-bar-stack">
              <span className="crm-bar value" style={{ height: `${(m.value / max) * 100}%` }} />
              <span className="crm-bar weighted" style={{ height: `${(m.weighted / max) * 100}%` }} />
              {m.won > 0 && <span className="crm-bar won" style={{ height: `${(m.won / max) * 100}%` }} />}
            </div>
            <span className="crm-bar-label">{label(m)}</span>
          </div>
        ))}
        <div className="crm-bar-legend muted tiny">
          <span><i className="value" />expected</span><span><i className="weighted" />weighted</span><span><i className="won" />won</span>
        </div>
      </div>
    </section>
  );
}

export function OutcomeDialog({ deal, stage, onDone, onClose }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const won = stage.kind === "won";
  async function send() {
    setBusy(true); setErr("");
    try { onDone(await api(`/crm-actions/deals/${deal.id}/move`, { method: "POST", body: { stage_id: stage.id, note } })); }
    catch (e) { setErr(e.message); } finally { setBusy(false); }
  }
  return (
    <div className="modal-backdrop" role="presentation" onClick={() => !busy && onClose()}>
      <div className={`hud modal${won ? "" : " alert"}`} role="dialog" aria-modal="true" aria-label={stage.name} onClick={(e) => e.stopPropagation()}>
        <header className="hud-head"><h2>{won ? "🎉 Won: " : "Lost: "}{deal.title}</h2></header>
        <p className="muted small">{won ? "What made it work? (optional, helps next time)" : "Why was it lost? (optional, shows up in the reports)"}</p>
        {!won && (
          <div className="pick-chips">
            {LOST_REASONS.map((r) => <button key={r} type="button" className={`chip${note === r ? "" : " off"}`} onClick={() => setNote(r)}>{r}</button>)}
          </div>
        )}
        <input value={note} maxLength={300} autoFocus placeholder={won ? "e.g. fast offer, good reference" : "or write your own"}
               onChange={(e) => setNote(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()} aria-label="Note" />
        <ErrorNote error={err} />
        <div className="modal-actions">
          <button className="btn ghost small" onClick={onClose} disabled={busy}>Cancel</button>
          <button className={`btn small ${won ? "primary" : "danger"}`} onClick={send} disabled={busy}>Mark as {stage.name.toLowerCase()}</button>
        </div>
      </div>
    </div>
  );
}

function DealCard({ d, stages, canEdit, onOpen, onMove, onDragStart }) {
  const [menu, setMenu] = useState(false);
  return (
    <article className={`crm-card${d.overdue_close ? " overdue" : ""}`} draggable={canEdit}
             onDragStart={(e) => { e.dataTransfer.setData("text/plain", String(d.id)); e.dataTransfer.effectAllowed = "move"; onDragStart(d); }}>
      <button type="button" className="crm-card-main" onClick={() => onOpen(d.id)}>
        <b className="crm-card-title">{d.title}</b>
        {d.company_name && <span className="meta">{d.company_name}</span>}
        <span className="crm-card-value">{fmtCHF(d.value)}<span className="meta"> · {d.probability_effective} %</span></span>
      </button>
      <div className="crm-card-foot">
        {d.expected_close && <span className={`meta${d.overdue_close ? " warn-text" : ""}`} title="Expected close">
          <Icon name="calendar" className="lab-ico" />{fmtDate(d.expected_close)}</span>}
        {d.next_step && <span className={`meta crm-card-next${d.next_step_due ? " warn-text" : ""}`} title={`Next step${d.next_step_at ? ` (${fmtDate(d.next_step_at)})` : ""}`}>→ {d.next_step}</span>}
        {canEdit && (
          <span className="crm-menu crm-card-move">
            <button type="button" className="btn ghost small" aria-haspopup="menu" aria-expanded={menu} aria-label={`Move ${d.title}`}
                    onClick={() => setMenu(!menu)}>Move ▾</button>
            {menu && (
              <span className="crm-menu-list" role="menu">
                {stages.filter((s) => s.id !== d.stage_id).map((s) => (
                  <button key={s.id} role="menuitem" className={s.kind === "lost" ? "danger" : ""}
                          onClick={() => { setMenu(false); onMove(d, s); }}>{s.name}</button>
                ))}
              </span>
            )}
          </span>
        )}
      </div>
    </article>
  );
}

function Board({ data, canEdit, onOpen, onMove }) {
  const [dragging, setDragging] = useState(null);
  const [over, setOver] = useState(null);
  const stages = data.stages;
  return (
    <div className="crm-board" role="list" aria-label="Pipeline">
      {stages.map((s) => (
        <section key={s.id} role="listitem" className={`crm-col k-${s.kind}${over === s.id ? " over" : ""}`}
                 aria-label={`${s.name}, ${s.count} deals`}
                 onDragOver={(e) => { if (dragging && dragging.stage_id !== s.id) { e.preventDefault(); setOver(s.id); } }}
                 onDragLeave={() => setOver((o) => (o === s.id ? null : o))}
                 onDrop={(e) => { e.preventDefault(); setOver(null); if (dragging && dragging.stage_id !== s.id) onMove(dragging, s); setDragging(null); }}>
          <header className="crm-col-head">
            <b>{s.name}</b><span className="mail-count plain">{s.count}</span>
            <span className="meta crm-col-sum" title={s.kind === "open" ? `Weighted ${fmtCHF(s.weighted)} at ${s.probability} %` : `Last ${data.closed_window_days} days`}>
              {fmtCHF(s.total, { compact: true })}{s.kind === "open" ? ` · ${s.probability} %` : ""}
            </span>
          </header>
          <div className="crm-col-body">
            {s.items.map((d) => <DealCard key={d.id} d={d} stages={stages} canEdit={canEdit} onOpen={onOpen} onMove={onMove}
                                          onDragStart={setDragging} />)}
            {s.items.length === 0 && <p className="crm-col-empty">{s.kind === "open" ? (canEdit ? "Drop a deal here" : "—")
              : `None in the last ${data.closed_window_days} days`}</p>}
          </div>
        </section>
      ))}
    </div>
  );
}

function DealList({ mine, q, onOpen }) {
  const [status, setStatus] = useState("open");
  const [sort, setSort] = useStoredState("alfred-crm-deal-sort", "close");
  const p = new URLSearchParams({ status, sort, limit: "200" });
  if (mine) p.set("mine", "true");
  if (q) p.set("q", q);
  const { data, error } = usePoll(() => api(`/crm/deals?${p}`), 30000, [p.toString()]);
  const items = data?.items || [];
  return (
    <div className="crm-deal-list">
      <div className="crm-list-tools">
        <div className="seg" role="tablist" aria-label="Status">
          {[["open", "Open"], ["won", "Won"], ["lost", "Lost"], ["all", "All"]].map(([k, l]) => (
            <button key={k} role="tab" aria-selected={status === k} className={status === k ? "active" : ""} onClick={() => setStatus(k)}>{l}</button>
          ))}
        </div>
        <select className="crm-sort" value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort">
          <option value="close">Close date</option><option value="value">Value</option>
          <option value="recent">Recently changed</option><option value="title">A–Z</option>
        </select>
        {data && <span className="muted tiny">{data.total} deals · {fmtCHF(items.reduce((a, d) => a + d.value, 0))}</span>}
      </div>
      <ErrorNote error={error} />
      {data && items.length === 0 && <Empty>No deals here.</Empty>}
      <div className="mail-list">
        {items.map((d) => (
          <button key={d.id} type="button" className="crm-row" onClick={() => onOpen(d.id)}>
            <span className={`crm-avatar deal k-${d.stage_kind}`} aria-hidden="true"><Icon name="crm" className="lab-ico" /></span>
            <span className="crm-row-main">
              <span className="crm-row-top"><b className="crm-row-name">{d.title}</b>
                <Tag kind={d.stage_kind === "won" ? "ok" : d.stage_kind === "lost" ? "bad" : ""}>{d.stage_name}</Tag></span>
              <span className="meta crm-row-sub">{[d.company_name, d.owner_name, d.next_step && `→ ${d.next_step}`].filter(Boolean).join(" · ")}</span>
            </span>
            <span className="crm-row-money"><b>{fmtCHF(d.value)}</b>
              <span className={`meta${d.overdue_close ? " warn-text" : ""}`}>{d.expected_close ? fmtDate(d.expected_close) : "no close date"}</span></span>
          </button>
        ))}
      </div>
    </div>
  );
}

export default function Pipeline({ status, mine, q, rev, onOpen, onToast, onChanged, onStages }) {
  const [mode, setMode] = useStoredState("alfred-crm-pipeline", "board");
  const p = new URLSearchParams();
  if (mine) p.set("mine", "true");
  if (q) p.set("q", q);
  const { data, error, reload } = usePoll(() => api(`/crm-actions/pipeline?${p}`), 30000, [p.toString(), rev]);
  const [outcome, setOutcome] = useState(null);
  const [err, setErr] = useState("");
  const canEdit = !!status?.can?.edit;

  async function move(deal, stage) {
    setErr("");
    if (stage.kind !== "open") { setOutcome({ deal, stage }); return; }
    try {
      const out = await api(`/crm-actions/deals/${deal.id}/move`, { method: "POST", body: { stage_id: stage.id } });
      reload(); onChanged?.(); onToast(`${deal.title} → ${stage.name}`, out.change_id);
    } catch (e) { setErr(e.message); }
  }
  return (
    <div className="crm-pipeline">
      <Forecast f={data?.forecast} />
      <div className="crm-list-tools">
        <div className="seg" role="tablist" aria-label="View">
          <button role="tab" aria-selected={mode === "board"} className={mode === "board" ? "active" : ""} onClick={() => setMode("board")}>Board</button>
          <button role="tab" aria-selected={mode === "list"} className={mode === "list" ? "active" : ""} onClick={() => setMode("list")}>List</button>
        </div>
        {status?.can?.settings && <button className="btn ghost small" onClick={onStages}><Icon name="settings" className="btn-ico" />Stages</button>}
        {mode === "board" && canEdit && <span className="muted tiny crm-hint">Drag a card to another stage, or use Move.</span>}
      </div>
      <ErrorNote error={error || err} />
      {mode === "board" ? (data && (
        data.stages.every((s) => s.count === 0) && !q ? (
          <div className="crm-empty">
            <Icon name="crm" className="crm-empty-ico" />
            <p><b>No deals yet.</b></p>
            <p className="muted small">Open a company and add a deal, or tell Alfred: “new deal for Acme: website relaunch, CHF 12’000, close end of November”.</p>
          </div>
        ) : <Board data={data} canEdit={canEdit} onOpen={onOpen} onMove={move} />
      )) : <DealList mine={mine} q={q} onOpen={onOpen} key={rev} />}
      {outcome && <OutcomeDialog deal={outcome.deal} stage={outcome.stage} onClose={() => setOutcome(null)}
        onDone={(out) => { setOutcome(null); reload(); onChanged?.(); onToast(`${outcome.deal.title}: ${outcome.stage.name.toLowerCase()}`, out.change_id); }} />}
    </div>
  );
}
