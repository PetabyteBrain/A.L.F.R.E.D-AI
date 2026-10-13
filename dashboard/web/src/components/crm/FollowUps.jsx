import { useState } from "react";
import { api } from "../../api.js";
import { fmtDate } from "../../format.js";
import { usePoll } from "../../hooks.js";
import { Empty, ErrorNote, Tag } from "../common.jsx";
import { Icon } from "../icons.jsx";

/* Follow-ups (CRM stage 5): what needs a nudge, grouped by kind, most pressing first. Open the record, draft an
   email, or put it off for a while. Alfred also sends these each weekday morning (briefing or Telegram). */

const TONE = { next_step_due: "bad", close_passed: "bad", invoice_overdue: "bad", no_next_step: "busy",
               stale: "busy", quote_expiring: "busy" };
const ICON = { next_step_due: "calendar", close_passed: "calendar", no_next_step: "crm", stale: "building",
               invoice_overdue: "doc", quote_expiring: "doc" };
const HELP = {
  next_step_due: "The planned next step's date has come.",
  close_passed: "The expected close date has passed: win it, lose it, or move the date.",
  no_next_step: "Open deals without a planned next step stall quietly.",
  stale: "Leads and prospects nobody has been in touch with for a while.",
  invoice_overdue: "Unpaid after the due date: a payment reminder may help.",
  quote_expiring: "Sent quotes about to run out.",
};

export default function FollowUps({ status, onOpen, onOpenDoc, onEmail, onToast, onChanged, onSettings }) {
  const [mine, setMine] = useState(true);
  const [snoozed, setSnoozed] = useState(false);
  const { data, error, reload } = usePoll(() => api(`/crm-actions/follow-ups?mine=${mine}&snoozed=${snoozed}`), 60000,
                                          [mine, snoozed]);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const items = data?.items || [];
  const groups = Object.keys(data?.types || {}).map((t) => ({ type: t, label: data.types[t], items: items.filter((i) => i.type === t) }))
    .filter((g) => g.items.length);

  async function snooze(i, days) {
    setBusy(i.key); setErr("");
    try {
      if (days) { await api("/crm-actions/follow-ups/snooze", { method: "POST", body: { key: i.key, days } }); onToast(`Put off for ${days} days: ${i.title}`); }
      else { await api(`/crm-actions/follow-ups/snooze?key=${encodeURIComponent(i.key)}`, { method: "DELETE" }); onToast(`Back on the list: ${i.title}`); }
      reload(); onChanged?.();
    } catch (e) { setErr(e.message); } finally { setBusy(""); }
  }
  const open = (i) => (i.entity === "billing" ? onOpenDoc(i.id) : onOpen(i.entity, i.id));
  const s = data?.settings;
  return (
    <div className="crm-followups">
      <div className="mail-toolbar">
        <div className="mail-filters">
          <b className="mail-title">Follow-ups</b>
          <div className="seg" role="tablist" aria-label="Whose">
            {[[true, "Mine"], [false, "Everyone's"]].map(([v, l]) => (
              <button key={l} role="tab" aria-selected={mine === v} className={mine === v ? "active" : ""} onClick={() => setMine(v)}>{l}</button>
            ))}
          </div>
          <label className="mail-unread-toggle"><input type="checkbox" checked={snoozed} onChange={(e) => setSnoozed(e.target.checked)} /> Show put off</label>
        </div>
        {status?.can?.settings && <button className="btn ghost small" onClick={onSettings}><Icon name="settings" className="btn-ico" />Settings</button>}
      </div>
      <p className="muted small">
        What needs a nudge{s ? `: leads with no contact for ${s.stale_days} days, deals without a next step or past their close date, overdue invoices` : ""}.
        {s?.nudges ? ` Alfred sends yours each weekday morning at ${s.nudge_time}, in the briefing if you get one.` : ""}
      </p>
      <ErrorNote error={error || err} />
      {data && items.length === 0 && (
        <div className="crm-empty">
          <Icon name="check" className="crm-empty-ico" />
          <p><b>{mine ? "Nothing to follow up on." : "Nobody has anything to follow up on."}</b></p>
          <p className="muted small">Every lead was contacted lately and every open deal has a next step.</p>
        </div>
      )}
      {groups.map((g) => (
        <section key={g.type} className="crm-fu-group">
          <h3 className="sub"><Icon name={ICON[g.type]} className="lab-ico" />{g.label}
            <span className="mail-count plain">{g.items.length}</span></h3>
          <p className="muted tiny">{HELP[g.type]}</p>
          <div className="crm-fu-list">
            {g.items.map((i) => (
              <article key={i.key} className={`crm-fu${i.snoozed_until ? " snoozed" : ""}`}>
                <button type="button" className="crm-fu-main" onClick={() => open(i)}>
                  <span className="crm-fu-top"><b>{i.title}</b>{i.company_name && i.entity === "deal" && <span className="meta">{i.company_name}</span>}
                    <Tag kind={TONE[i.type]}>{i.label}</Tag></span>
                  <span className="meta">{i.detail}{!mine && i.owner_name ? ` · ${i.owner_name}` : ""}
                    {i.snoozed_until ? ` · put off until ${fmtDate(i.snoozed_until)}` : ""}</span>
                </button>
                <div className="crm-fu-actions">
                  {status?.can?.edit && i.entity !== "billing" && (
                    <button className="btn small" onClick={() => onEmail(i.entity, { id: i.id, name: i.title })}>
                      <Icon name="mail" className="btn-ico" />Email</button>
                  )}
                  {i.snoozed_until ? (
                    <button className="btn ghost small" disabled={busy === i.key} onClick={() => snooze(i, 0)}>Bring back</button>
                  ) : (
                    <select className="crm-sort" value="" disabled={busy === i.key} aria-label="Put off"
                            onChange={(e) => e.target.value && snooze(i, Number(e.target.value))}>
                      <option value="">Put off…</option>
                      <option value="1">1 day</option>
                      <option value="3">3 days</option>
                      <option value="7">1 week</option>
                      <option value="14">2 weeks</option>
                      <option value="30">1 month</option>
                    </select>
                  )}
                </div>
              </article>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
