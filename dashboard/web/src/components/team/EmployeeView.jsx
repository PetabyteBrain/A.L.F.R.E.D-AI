import { useState } from "react";
import { api } from "../../api.js";
import { fmtAgo, fmtCHF, fmtDate, fmtPhone, fmtTime } from "../../format.js";
import { usePoll } from "../../hooks.js";
import { ErrorNote, Tag } from "../common.jsx";
import { Icon } from "../icons.jsx";
import { Avatar } from "../crm/RecordView.jsx";
import { ChecklistCard } from "./Checklists.jsx";
import LinkedItems from "../boards/LinkedItems.jsx";

/* One employee: details, holiday balance, absences, onboarding/offboarding, responsibilities, hours and history.
   Every change to the details is sent for approval; ticking off checklist steps applies directly. */

export const STATUS = { active: ["Active", "ok"], starting: ["Starting soon", "busy"], onboarding: ["Onboarding", "busy"],
                        leaving: ["Leaving", "busy"], former: ["Former", "bad"] };
export const ABSENCE_TONE = { pending: "busy", approved: "ok", rejected: "bad", cancelled: "" };

function Fact({ label, children, href }) {
  if (children === null || children === undefined || children === "") return null;
  return (
    <div className="kv crm-fact"><span>{label}</span>
      {href ? <a href={href}>{children}</a> : <b>{children}</b>}
    </div>
  );
}

export function Balance({ b, compact = false }) {
  const total = Math.max(b.total, 0.5);
  const pct = (x) => `${Math.max(0, Math.min(100, (x / total) * 100))}%`;
  return (
    <div className={`team-balance${compact ? " compact" : ""}`}>
      <div className="team-balance-head">
        <span>Holidays {b.year}</span>
        <b className={b.remaining < 0 ? "warn-text" : ""}>{b.remaining} <small>of {b.total} days left</small></b>
      </div>
      <div className="team-meter" role="img" aria-label={`${b.taken} taken, ${b.planned} planned, ${b.pending} requested, ${b.remaining} left`}>
        <span className="taken" style={{ width: pct(b.taken) }} />
        <span className="planned" style={{ width: pct(b.planned) }} />
        <span className="pending" style={{ width: pct(b.pending) }} />
      </div>
      <div className="team-meter-legend muted tiny">
        <span><i className="taken" />{b.taken} taken</span><span><i className="planned" />{b.planned} planned</span>
        {b.pending > 0 && <span><i className="pending" />{b.pending} waiting for approval</span>}
        {b.adjust ? <span>{b.adjust > 0 ? "+" : ""}{b.adjust} carried over / corrected</span> : null}
      </div>
    </div>
  );
}

function TimeCard({ id }) {
  const { data: t } = usePoll(() => api(`/team/employees/${id}/time`), 0, [id]);
  if (!t || !t.configured) return null;
  return (
    <div className="crm-people crm-time">
      <h3 className="sub">Time tracked</h3>
      {t.error ? <p className="muted small">{t.error}</p> : (
        <>
          <div className="crm-time-nums">
            <div><b>{t.week} h</b><span className="meta">this week</span></div>
            <div><b>{t.month} h</b><span className="meta">this month</span></div>
          </div>
          {t.by_client.length > 0 && <p className="small">{t.by_client.slice(0, 6).map((c) => `${c.name} ${c.hours} h`).join(" · ")}</p>}
          {t.month === 0 && <p className="muted tiny">Nothing found for “{t.matched_by}” in the time-tracking app (set their
            time-tracking name in Edit).</p>}
        </>
      )}
    </div>
  );
}

export function awayText(a) {
  if (a.half_day) return `${a.label} (½ day)`;
  return a.until === new Date().toLocaleDateString("sv-SE") ? `${a.label} today` : `${a.label} until ${fmtDate(a.until)}`;
}

export function absenceWhen(a) {
  return a.start_date === a.end_date ? `${fmtDate(a.start_date)}${a.half_day ? " (half day)" : ""}` : `${fmtDate(a.start_date)} – ${fmtDate(a.end_date)}`;
}

export default function EmployeeView({ id, status, viewer, isMePage, onBack, onOpen, onEdit, onAbsence, onAccount, onToast, onChanged }) {
  const { data: e, error, reload } = usePoll(() => api(`/team/employees/${id}`), 0, [id]);
  const [menu, setMenu] = useState(false);
  const [showPast, setShowPast] = useState(false);
  const [hist, setHist] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(0);
  const refresh = () => { reload(); onChanged?.(); };

  async function cancelAbsence(a) {
    setBusy(a.id); setErr("");
    try {
      const out = await api(`/team/absences/${a.id}`, { method: "DELETE" });
      onToast(out.approval ? "Cancelling it waits for approval." : "Request withdrawn.");
      refresh();
    } catch (x) { setErr(x.message); } finally { setBusy(0); }
  }
  async function startChecklist(kind) {
    setMenu(false); setErr("");
    try { await api(`/team/employees/${id}/checklists`, { method: "POST", body: { kind } }); onToast(`${kind[0].toUpperCase()}${kind.slice(1)} started`); refresh(); }
    catch (x) { setErr(x.message); }
  }

  if (!e) return <div className="crm-detail"><ErrorNote error={error} />{!error && <p className="muted">Loading…</p>}</div>;
  const [label, tone] = STATUS[e.status] || [e.status, ""];
  const today = new Date().toLocaleDateString("sv-SE");          // local YYYY-MM-DD
  const upcoming = e.absences.filter((a) => a.end_date >= today && ["pending", "approved"].includes(a.status));
  const past = e.absences.filter((a) => !upcoming.includes(a)).reverse();
  const openLists = e.checklists.filter((c) => !c.closed_at);
  const doneLists = e.checklists.filter((c) => c.closed_at);
  return (
    <div className="crm-detail">
      <div className="mail-view-head">
        <div className="mail-view-nav">{onBack && <button className="btn ghost small" onClick={onBack}>‹ Back</button>}</div>
        <div className="mail-view-tools">
          {e.can.request_absence && <button className="btn small" onClick={() => onAbsence(e)}><Icon name="calendar" className="btn-ico" />
            {e.is_me ? "Request time off" : "Record absence"}</button>}
          {e.can.manage && <button className="btn small" onClick={() => onEdit(e)}>Edit</button>}
          {e.can.manage && (
            <div className="crm-menu">
              <button className="btn ghost small" aria-haspopup="menu" aria-expanded={menu} onClick={() => setMenu(!menu)}>More ▾</button>
              {menu && (
                <div className="crm-menu-list" role="menu">
                  {!openLists.some((c) => c.kind === "onboarding") && <button role="menuitem" onClick={() => startChecklist("onboarding")}>Start onboarding</button>}
                  {!openLists.some((c) => c.kind === "offboarding") && (
                    e.end_date ? <button role="menuitem" onClick={() => startChecklist("offboarding")}>Start offboarding</button>
                      : <button role="menuitem" onClick={() => { setMenu(false); onEdit({ ...e, offboard: true }); }}>Offboard (set last day)…</button>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <header className="crm-head">
        <Avatar entity="contact" name={e.name} />
        <div className="crm-head-main">
          <h3 className="crm-title">{e.name}{isMePage && <span className="muted"> (you)</span>}</h3>
          <div className="crm-head-meta">
            <span>{[e.job_title, e.department].filter(Boolean).join(", ")}</span>
            <Tag kind={tone}>{label}</Tag>
            {e.away && <Tag kind="busy">{awayText(e.away)}</Tag>}
          </div>
        </div>
      </header>
      <ErrorNote error={err} />
      {e.pending_changes.length > 0 && (
        <div className="crm-callout">
          <span className="crm-callout-label">Waiting for approval</span>
          {e.pending_changes.map((p) => <b key={p.id}>#{p.id} {p.summary}</b>)}
        </div>
      )}

      <div className="crm-detail-grid">
        <div className="crm-facts-col">
          <Balance b={e.balance} />
          <div className="crm-facts">
            <Fact label="Email" href={e.email ? `mailto:${e.email}` : null}>{e.email}</Fact>
            <Fact label="Phone" href={e.phone ? `tel:${e.phone}` : null}>{fmtPhone(e.phone)}</Fact>
            <Fact label="Employment">{`${e.employment_label}${e.workload !== 100 ? `, ${e.workload} %` : ""}`}</Fact>
            <Fact label="First day">{e.start_date ? fmtDate(e.start_date) : null}</Fact>
            <Fact label="Last day">{e.end_date ? fmtDate(e.end_date) : null}</Fact>
            {e.manager_id && <div className="kv crm-fact"><span>Manager</span>
              {onOpen ? <button type="button" className="linkish" onClick={() => onOpen(e.manager_id)}>{e.manager_name}</button> : <b>{e.manager_name}</b>}</div>}
            {e.reports.length > 0 && <div className="kv crm-fact"><span>Team</span><b>{e.reports.map((r, i) => (
              <span key={r.id}>{i > 0 && ", "}{onOpen ? <button type="button" className="linkish" onClick={() => onOpen(r.id)}>{r.name}</button> : r.name}</span>))}</b></div>}
            <div className="kv crm-fact"><span>Alfred account</span>
              {e.user_id && onAccount ? <button type="button" className="linkish" onClick={() => onAccount(e.user_id)}>{e.user_name} (roles, logins…)</button>
                : <b>{e.user_name || "—"}</b>}</div>
            <Fact label="Holidays a year">{`${e.holiday_days} days`}</Fact>
            {status?.timetrack && <Fact label="Time tracking">{e.timetrack_user || null}</Fact>}
          </div>
          {e.notes && <div className="crm-notes"><h3 className="sub">Notes</h3><p className="pre">{e.notes}</p></div>}
          {e.work && (
            <div className="crm-people">
              <h3 className="sub">Responsibilities</h3>
              <div className="crm-time-nums">
                <div><b>{e.work.open_tasks}</b><span className="meta">open tasks</span></div>
                {e.work.companies !== undefined && <div><b>{e.work.companies}</b><span className="meta">clients owned</span></div>}
                {e.work.open_deals !== undefined && <div><b>{e.work.open_deals}</b><span className="meta">open deals · {fmtCHF(e.work.open_deal_value, { compact: true })}</span></div>}
              </div>
            </div>
          )}
          {status?.timetrack && <TimeCard id={id} />}
          {e.history.length > 0 && (
            <section className="crm-history">
              <button type="button" className="btn ghost small" onClick={() => setHist(!hist)} aria-expanded={hist}>History {hist ? "▴" : "▾"}</button>
              {hist && (
                <ul className="crm-history-list">
                  {e.history.map((h) => (
                    <li key={h.id}><span>{h.action === "create" ? "Added" : `Changed ${h.fields.join(", ")}`}{h.by ? `, approved by ${h.by}` : ""}</span>
                      <span className="meta" title={fmtTime(h.ts)}>{fmtAgo(h.ts)}</span></li>
                  ))}
                </ul>
              )}
            </section>
          )}
        </div>

        <div className="team-col">
          <section className="team-card">
            <h3 className="sub"><Icon name="calendar" className="lab-ico" />Time off
              {e.can.request_absence && <button className="btn ghost small" onClick={() => onAbsence(e)}><Icon name="plus" className="btn-ico" />Add</button>}</h3>
            {upcoming.length === 0 && <p className="muted small">Nothing planned.</p>}
            <ul className="team-absences">
              {upcoming.map((a) => (
                <li key={a.id} className={`k-${a.kind}`}>
                  <span className="team-abs-kind">{a.kind_label}</span>
                  <span className="team-abs-when">{absenceWhen(a)}<span className="meta"> · {a.days} day{a.days === 1 ? "" : "s"}</span></span>
                  <Tag kind={ABSENCE_TONE[a.status]}>{a.status === "pending" ? "waiting for approval" : a.status}</Tag>
                  {(e.is_me || e.can.manage) && (
                    <button className="btn ghost small" disabled={busy === a.id} onClick={() => cancelAbsence(a)}>
                      {a.status === "pending" ? "Withdraw" : "Cancel…"}</button>
                  )}
                  {a.note && <span className="meta team-abs-note">“{a.note}”</span>}
                </li>
              ))}
            </ul>
            {past.length > 0 && (
              <>
                <button type="button" className="btn ghost small" onClick={() => setShowPast(!showPast)} aria-expanded={showPast}>
                  Earlier and declined ({past.length}) {showPast ? "▴" : "▾"}</button>
                {showPast && (
                  <ul className="team-absences past">
                    {past.map((a) => (
                      <li key={a.id} className={`k-${a.kind}`}>
                        <span className="team-abs-kind">{a.kind_label}</span>
                        <span className="team-abs-when">{absenceWhen(a)}<span className="meta"> · {a.days} d</span></span>
                        <Tag kind={ABSENCE_TONE[a.status]}>{a.status}</Tag>
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </section>
          {e.user_id && <LinkedItems me={viewer} filter={`user_id=${e.user_id}`} title={e.is_me ? "My board items" : "Board items"}
                                     empty="Nothing assigned on the boards." />}
          {openLists.map((c) => <ChecklistCard key={c.id} list={c} status={status} canManage={e.can.manage} onChanged={refresh} />)}
          {doneLists.length > 0 && <p className="muted tiny">{doneLists.map((c) => `${c.kind[0].toUpperCase()}${c.kind.slice(1)} completed ${fmtAgo(c.closed_at)}`).join(" · ")}</p>}
        </div>
      </div>
    </div>
  );
}
